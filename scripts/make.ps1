param (
    [Parameter(Position = 0)]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host "  Blood Donation Platform - Development & Verification Commands" -ForegroundColor Cyan
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host "  .\make.cmd setup          Install dependencies and create .env"
    Write-Host "  .\make.cmd db-up          Start database and mailpit containers"
    Write-Host "  .\make.cmd db-down        Stop containers"
    Write-Host "  .\make.cmd migrate        Run Alembic database migrations"
    Write-Host "  .\make.cmd seed           Seed development database"
    Write-Host "  .\make.cmd run            Run Uvicorn development server"
    Write-Host "  .\make.cmd test           Run all pytest tests"
    Write-Host "  .\make.cmd test-critical  Run critical tests only"
    Write-Host "  .\make.cmd lint           Run ruff (check + format) and mypy"
    Write-Host "  .\make.cmd audit          Run pip-audit vulnerability check"
    Write-Host "  .\make.cmd check          Run full quality check (lint + test)"
    Write-Host "=================================================================" -ForegroundColor Cyan
}

switch ($Target.ToLower()) {
    "help" {
        Show-Help
    }
    "setup" {
        Write-Host "--> Running uv sync..." -ForegroundColor Green
        uv sync
        if (-not (Test-Path ".env")) {
            Copy-Item ".env.example" ".env"
            Write-Host "--> Created .env from .env.example" -ForegroundColor Green
        }
    }
    "db-up" {
        Write-Host "--> Starting database and mailpit containers..." -ForegroundColor Green
        docker compose up -d db db_test mailpit
    }
    "db-down" {
        Write-Host "--> Stopping containers..." -ForegroundColor Green
        docker compose down
    }
    "migrate" {
        Write-Host "--> Running Alembic migrations..." -ForegroundColor Green
        uv run alembic upgrade head
    }
    "seed" {
        Write-Host "--> Seeding development database..." -ForegroundColor Green
        uv run python -m app.cli seed
    }
    "run" {
        Write-Host "--> Starting Uvicorn development server..." -ForegroundColor Green
        uv run uvicorn app.main:app --reload
    }
    "test" {
        Write-Host "--> Running pytest..." -ForegroundColor Green
        if (Test-Path "tests") {
            uv run pytest
        } else {
            Write-Host "tests/ directory does not exist yet (scheduled for P0.12); skipping." -ForegroundColor Yellow
        }
    }
    "test-critical" {
        Write-Host "--> Running critical tests..." -ForegroundColor Green
        if (Test-Path "tests") {
            uv run pytest -m critical
        } else {
            Write-Host "tests/ directory does not exist yet (scheduled for P0.12); skipping." -ForegroundColor Yellow
        }
    }
    "lint" {
        Write-Host "--> Running ruff check..." -ForegroundColor Green
        uv run ruff check .
        Write-Host "--> Running ruff format --check..." -ForegroundColor Green
        uv run ruff format --check .
        if (Test-Path "app") {
            Write-Host "--> Running mypy app..." -ForegroundColor Green
            uv run mypy app
        } else {
            Write-Host "app/ directory does not exist yet (scheduled for P0.12); skipping mypy app." -ForegroundColor Yellow
        }
    }
    "audit" {
        Write-Host "--> Running pip-audit..." -ForegroundColor Green
        uv run pip-audit
    }
    "check" {
        Write-Host "=================================================================" -ForegroundColor Cyan
        Write-Host "  Running unified quality check (check: lint + test)" -ForegroundColor Cyan
        Write-Host "=================================================================" -ForegroundColor Cyan
        & $PSCommandPath lint
        & $PSCommandPath test
        Write-Host "--> All checks PASSED!" -ForegroundColor Green
    }
    default {
        Write-Error "Unknown target: '$Target'. Run '.\make.cmd help' for options."
    }
}
