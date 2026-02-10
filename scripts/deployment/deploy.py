"""
Deployment scripts for managing application releases and infrastructure.
"""

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


def build_docker_image(tag="latest"):
    """
    Build Docker image for the application.
    
    Args:
        tag: Docker image tag
    """
    logger.info(f"Building Docker image with tag {tag}")
    cmd = [
        "docker", "build",
        "-t", f"futurapredict:{tag}",
        "-f", "infrastructure/docker/Dockerfile",
        "."
    ]
    subprocess.run(cmd, check=True)


def push_docker_image(tag="latest"):
    """
    Push Docker image to registry.
    
    Args:
        tag: Docker image tag
    """
    logger.info(f"Pushing Docker image with tag {tag}")
    cmd = ["docker", "push", f"futurapredict:{tag}"]
    subprocess.run(cmd, check=True)


def deploy_kubernetes(environment="production"):
    """
    Deploy application to Kubernetes.
    
    Args:
        environment: Target environment
    """
    logger.info(f"Deploying to Kubernetes environment: {environment}")
    # Implementation here


def run_migrations():
    """
    Run database migrations.
    """
    logger.info("Running database migrations")
    cmd = ["python", "manage.py", "migrate"]
    subprocess.run(cmd, check=True)


def collect_static():
    """
    Collect static files.
    """
    logger.info("Collecting static files")
    cmd = ["python", "manage.py", "collectstatic", "--noinput"]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    build_docker_image()
    run_migrations()
    collect_static()
