import boto3
import os
from botocore.exceptions import ClientError
from logger import get_logger

log = get_logger(__name__)

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")

s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)


def upload_file_to_s3(file_obj, filename, content_type):
    try:
        s3_client.upload_fileobj(
            file_obj,
            AWS_S3_BUCKET_NAME,
            filename,
            ExtraArgs={"ContentType": content_type},
        )
        return f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{filename}"
    except ClientError as e:
        log.error("s3_upload_failed", filename=filename, error=str(e), exc_info=True)
        raise


def generate_presigned_url(filename, expiration=3600):
    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": AWS_S3_BUCKET_NAME, "Key": filename},
            ExpiresIn=expiration,
        )
        return url
    except ClientError as e:
        log.error("presigned_url_failed", filename=filename, error=str(e), exc_info=True)
        raise
