---
name: deploy
description: Deployment workflow for FastAPI applications to AWS or Docker
---

# Deploy Skill

When deploying a FastAPI application:

1. Run the **testing** skill first to verify all tests pass
2. Build Docker image: `docker build -t app .`
3. Tag with version from pyproject.toml
4. Push to container registry

MANDATORY: Always run the testing skill before deployment. Do not deploy if tests fail.

## AWS Deployment
- Use ECS Fargate for containerized deployment
- Health check endpoint: `/health`
- Environment variables via AWS Secrets Manager
