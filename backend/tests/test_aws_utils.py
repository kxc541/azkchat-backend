import pytest
from unittest.mock import MagicMock, patch
import aws_utils


class TestUploadFileToS3:
    def test_returns_correct_url(self):
        mock_s3 = MagicMock()
        aws_utils.s3_client = mock_s3
        aws_utils.AWS_S3_BUCKET_NAME = "my-bucket"
        aws_utils.AWS_REGION = "us-east-1"

        file_obj = MagicMock()
        url = aws_utils.upload_file_to_s3(file_obj, "logos/logo.png", "image/png")

        assert url == "https://my-bucket.s3.us-east-1.amazonaws.com/logos/logo.png"
        mock_s3.upload_fileobj.assert_called_once()

    def test_reraises_on_client_error(self):
        from botocore.exceptions import ClientError

        mock_s3 = MagicMock()
        mock_s3.upload_fileobj.side_effect = ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "bucket missing"}},
            "PutObject",
        )
        aws_utils.s3_client = mock_s3

        with pytest.raises(ClientError):
            aws_utils.upload_file_to_s3(MagicMock(), "file.pdf", "application/pdf")


class TestGeneratePresignedUrl:
    def test_returns_url(self):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/signed"
        aws_utils.s3_client = mock_s3

        url = aws_utils.generate_presigned_url("logos/logo.png")
        assert url == "https://s3.amazonaws.com/signed"

    def test_reraises_on_client_error(self):
        from botocore.exceptions import ClientError

        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "key missing"}},
            "GetObject",
        )
        aws_utils.s3_client = mock_s3

        with pytest.raises(ClientError):
            aws_utils.generate_presigned_url("missing/file.pdf")
