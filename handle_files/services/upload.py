import boto3
import os
from dotenv import load_dotenv
from abc import ABC, abstractmethod
import logging

load_dotenv()

class UploadService(ABC):
    """
    Abstract base class for file upload services.
    This serves as an interface that all upload service implementations must follow.
    """
    
    @abstractmethod
    def upload_file(self, file_path, object_name)->str:
        """
        Upload a file to the storage service.
        
        Args:
            file_path (str): Path to the file to upload
            object_name (str): Name to give the file in the storage service
            
        Returns:
            str: URL or path to the uploaded file
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            Exception: For other upload errors
        """
        pass
    
    @abstractmethod
    def download_file(self, object_name, download_path):
        """
        Download a file from the storage service.
        
        Args:
            object_name (str): Name of the file in the storage service
            download_path (str): Path to save the downloaded file
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            Exception: For other download errors
        """
        pass
class AwsUploadService(UploadService):
    """
    Implementation of UploadService for AWS S3.
    """
    
    def __init__(self):
        """
        Initialize the AWS S3 client with credentials from environment variables.
        """
        try:
            self.s3 = boto3.client('s3',
                                  aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                                  aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                                  region_name=os.getenv('AWS_REGION'))
            self.bucket_name = os.getenv('AWS_BUCKET_NAME')
            
            if not all([os.environ.get('AWS_ACCESS_KEY_ID'), 
                        os.environ.get('AWS_SECRET_ACCESS_KEY'), 
                        os.environ.get('AWS_REGION'),
                        os.environ.get('AWS_BUCKET_NAME')]):
                raise ValueError("Missing required AWS environment variables")
                
        except Exception as e:
            logging.error(f"Failed to initialize AWS S3 client: {str(e)}")
            raise
    
    def upload_file(self, file_path, object_name):
        """
        Upload a file to AWS S3.
        
        Args:
            file_path (str): Path to the file to upload
            object_name (str): Name to give the file in S3
            
        Returns:
            str: URL to the uploaded file
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            Exception: For other upload errors
        """
        try:    
            print(file_path, object_name)
            # Create the S3 key with the proper path structure
            s3_key = f"uploads/tickets/{object_name}"
            # Upload the file to S3
            self.s3.upload_file(file_path, self.bucket_name, s3_key)
            
            # Generate the URL for the uploaded file
            url = f"https://{self.bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{s3_key}"
            return url
            
        except FileNotFoundError:
            logging.error(f"File not found: {file_path}")
            raise
        except Exception as e:
            logging.error(f"Error uploading file to S3: {str(e)}")
            raise
    
    def download_file(self, object_name, download_path):
        """
        Download a file from AWS S3.
        
        Args:
            object_name (str): Name of the file in S3
            download_path (str): Path to save the downloaded file
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            Exception: For other download errors
        """
        try:
            s3_key = f"uploads/tickets/{object_name}"
            return self.s3.download_file(self.bucket_name, s3_key, download_path)
        except FileNotFoundError:
            logging.error(f"File not found in S3: {object_name}")
            raise
        except Exception as e:
            logging.error(f"Error downloading file from S3: {str(e)}")
            raise



class UploadServiceFactory:
    """
    Factory class for creating upload services.
    """
    @staticmethod
    def create()->UploadService:
        return AwsUploadService()

