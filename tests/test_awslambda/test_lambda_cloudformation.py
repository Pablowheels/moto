import boto3
import io
import sure  # noqa
import zipfile
from botocore.exceptions import ClientError
from moto import mock_cloudformation, mock_iam, mock_lambda, mock_s3
from nose.tools import assert_raises
from string import Template
from uuid import uuid4


def _process_lambda(func_str):
    zip_output = io.BytesIO()
    zip_file = zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED)
    zip_file.writestr("lambda_function.py", func_str)
    zip_file.close()
    zip_output.seek(0)
    return zip_output.read()


def get_zip_file():
    pfunc = """
def lambda_handler1(event, context):
    return event
def lambda_handler2(event, context):
    return event
"""
    return _process_lambda(pfunc)


template = Template(
    """{
    "AWSTemplateFormatVersion": "2010-09-09",
    "Resources": {
        "LF3ABOV": {
            "Type": "AWS::Lambda::Function",
            "Properties": {
                "Handler": "$handler",
                "Role": "$role_arn",
                "Runtime": "$runtime",
                "Code": {
                    "S3Bucket": "$bucket_name",
                    "S3Key": "$key"
                },
            }
        }
    }
}"""
)


@mock_cloudformation
@mock_lambda
@mock_s3
def test_lambda_can_be_updated_by_cloudformation():
    s3 = boto3.client("s3", "us-east-1")
    cf = boto3.client("cloudformation", region_name="us-east-1")
    lmbda = boto3.client("lambda", region_name="us-east-1")
    body2, stack = create_stack(cf, s3)
    created_fn_name = get_created_function_name(cf, stack)
    # Verify function has been created
    created_fn = lmbda.get_function(FunctionName=created_fn_name)
    created_fn["Configuration"]["Handler"].should.equal(
        "lambda_function.lambda_handler1"
    )
    created_fn["Configuration"]["Runtime"].should.equal("python3.7")
    created_fn["Code"]["Location"].should.match("/test1.zip")
    # Update CF stack
    cf.update_stack(StackName="teststack", TemplateBody=body2)
    updated_fn_name = get_created_function_name(cf, stack)
    # Verify function has been updated
    updated_fn = lmbda.get_function(FunctionName=updated_fn_name)
    updated_fn["Configuration"]["FunctionArn"].should.equal(
        created_fn["Configuration"]["FunctionArn"]
    )
    updated_fn["Configuration"]["Handler"].should.equal(
        "lambda_function.lambda_handler2"
    )
    updated_fn["Configuration"]["Runtime"].should.equal("python3.8")
    updated_fn["Code"]["Location"].should.match("/test2.zip")


@mock_cloudformation
@mock_lambda
@mock_s3
def test_lambda_can_be_deleted_by_cloudformation():
    s3 = boto3.client("s3", "us-east-1")
    cf = boto3.client("cloudformation", region_name="us-east-1")
    lmbda = boto3.client("lambda", region_name="us-east-1")
    _, stack = create_stack(cf, s3)
    created_fn_name = get_created_function_name(cf, stack)
    # Delete Stack
    cf.delete_stack(StackName=stack["StackId"])
    # Verify function was deleted
    with assert_raises(ClientError) as e:
        lmbda.get_function(FunctionName=created_fn_name)
    e.exception.response["Error"]["Code"].should.equal("ResourceNotFoundException")


def create_stack(cf, s3):
    bucket_name = str(uuid4())
    s3.create_bucket(Bucket=bucket_name)
    s3.put_object(Bucket=bucket_name, Key="test1.zip", Body=get_zip_file())
    s3.put_object(Bucket=bucket_name, Key="test2.zip", Body=get_zip_file())
    body1 = get_template(bucket_name, "1", "python3.7")
    body2 = get_template(bucket_name, "2", "python3.8")
    stack = cf.create_stack(StackName="teststack", TemplateBody=body1)
    return body2, stack


def get_created_function_name(cf, stack):
    res = cf.list_stack_resources(StackName=stack["StackId"])
    return res["StackResourceSummaries"][0]["PhysicalResourceId"]


def get_template(bucket_name, version, runtime):
    key = "test" + version + ".zip"
    handler = "lambda_function.lambda_handler" + version
    return template.substitute(
        bucket_name=bucket_name,
        key=key,
        handler=handler,
        role_arn=get_role_arn(),
        runtime=runtime,
    )


def get_role_arn():
    with mock_iam():
        iam = boto3.client("iam", region_name="us-west-2")
        try:
            return iam.get_role(RoleName="my-role")["Role"]["Arn"]
        except ClientError:
            return iam.create_role(
                RoleName="my-role",
                AssumeRolePolicyDocument="some policy",
                Path="/my-path/",
            )["Role"]["Arn"]
