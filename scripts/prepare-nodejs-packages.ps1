<#
.SYNOPSIS
    Prepare Node.js 22 LTS binary and MCP npm packages (QA/Offline mode)
.DESCRIPTION
    QA/Offline mode, all deps pre-downloaded to packages/
    This script does two things:
    1. Download Node.js 22 LTS binary tarball to packages/nodejs/
    2. Use Docker container (node:22-slim) to pre-install 40 MCP npm packages globally,
       export as tarball to packages/npm-pkgs/
    Requirement: 39/40 system MCPs depend on npx; git MCP was changed to npx too (40 total)
.NOTES
    Prerequisites: Docker installed, internet access (first run)
    After run: packages/nodejs/ and packages/npm-pkgs/ ready for Dockerfile.qa / Dockerfile.offline
#>

$ErrorActionPreference = "Stop"

# Project root (script in scripts/, root is one level up)
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$packagesDir = Join-Path $projectRoot "packages"
$nodejsDir = Join-Path $packagesDir "nodejs"
$npmPkgsDir = Join-Path $packagesDir "npm-pkgs"

# Create directories
New-Item -ItemType Directory -Force -Path $nodejsDir | Out-Null
New-Item -ItemType Directory -Force -Path $npmPkgsDir | Out-Null

# Node.js 22 LTS version (use .tar.gz instead of .tar.xz, python:3.12-slim has no xz)
# v22.23.1 (2026-06-23): fix npm 10.9.8 minizlib incompatibility with Node 22.11
$nodeVersion = "v22.23.1"
$nodeTarballName = "node-$nodeVersion-linux-x64.tar.gz"
$nodeUrl = "https://nodejs.org/dist/$nodeVersion/$nodeTarballName"
$nodeTarballPath = Join-Path $nodejsDir $nodeTarballName

Write-Host "========== 1. Download Node.js $nodeVersion binary ==========" -ForegroundColor Cyan
if (Test-Path $nodeTarballPath) {
    Write-Host "Exists: $nodeTarballName, skip download" -ForegroundColor Yellow
} else {
    Write-Host "Downloading: $nodeUrl"
    Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeTarballPath -UseBasicParsing
    Write-Host "Done: $nodeTarballPath" -ForegroundColor Green
}

# 40 MCP npm packages (matches scripts/init.sql system MCPs)
$npmPackages = @(
    "@modelcontextprotocol/server-fetch",
    "@modelcontextprotocol/server-filesystem",
    "@modelcontextprotocol/server-sequentialthinking",
    "@modelcontextprotocol/server-github",
    "@notionhq/notion-mcp-server",
    "mcp-obsidian",
    "@sooperset/mcp-atlassian",
    "@elastic/mcp-server-elasticsearch",
    "@phuongcao/mcp-server-wikipedia",
    "mcp-hacker-news",
    "mcp-server-newsapi",
    "mcp-server-stackoverflow",
    "mcp-server-neo4j",
    "mcp-server-duckdb",
    "mcp-server-alpha-vantage",
    "mcp-server-wolfram-alpha",
    "mcp-server-deepl",
    "mcp-server-rss",
    "@modelcontextprotocol/server-git",
    "@modelcontextprotocol/server-gitlab",
    "@anthropic-ai/chrome-mcp",
    "mcp-server-npm-search",
    "mcp-server-sourcegraph",
    "mcp-server-filesystem-search",
    "@modelcontextprotocol/server-gdrive",
    "mcp-server-airtable",
    "@anaisbetts/mcp-youtube",
    "@enescinar/twitter-mcp",
    "mcp-server-reddit",
    "mcp-server-mongodb",
    "@supabase/mcp-server-supabase",
    "mcp-server-bigquery",
    "mcp-server-clickhouse",
    "mcp-server-snowflake",
    "mcp-server-mapbox",
    "mcp-server-openweather",
    "@modelcontextprotocol/server-aws-kb-retrieval",
    "mcp-server-calculator",
    "mcp-server-markdown",
    "mcp-server-pdf-tools"
)

Write-Host ""
Write-Host "========== 2. Pre-install $($npmPackages.Count) MCP npm packages via Docker ==========" -ForegroundColor Cyan

# Check Docker availability
$dockerAvailable = $false
try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    if ($LASTEXITCODE -eq 0 -and $dockerVersion) {
        $dockerAvailable = $true
        Write-Host "Docker available (Server version: $dockerVersion)" -ForegroundColor Green
    }
} catch { }

if (-not $dockerAvailable) {
    Write-Host "ERROR: Docker not available, cannot pre-install npm packages" -ForegroundColor Red
    Write-Host "Please start Docker Desktop first, then re-run this script" -ForegroundColor Yellow
    exit 1
}

# Pull node:22-slim if not present
$imageCheck = docker image ls node:22-slim --format "{{.Repository}}:{{.Tag}}" 2>$null
if (-not $imageCheck) {
    Write-Host "Pulling node:22-slim image..." -ForegroundColor Yellow
    docker pull node:22-slim
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pull node:22-slim failed" -ForegroundColor Red
        exit 1
    }
}

# Create temp container to pre-install npm packages
$tempContainer = "npm-preparer-$(Get-Random -Maximum 99999)"
$globalTarball = "node-global.tar.gz"
$globalTarballPath = Join-Path $npmPkgsDir $globalTarball

Write-Host "Creating temp container: $tempContainer" -ForegroundColor Yellow
docker run --name $tempContainer -d node:22-slim tail -f /dev/null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: create temp container failed" -ForegroundColor Red
    exit 1
}

try {
    # Install npm packages one by one (single failure does not block others)
    Write-Host "Pre-installing $($npmPackages.Count) npm packages globally (one by one)..." -ForegroundColor Yellow
    Write-Host "  (first run may take 5-10 minutes, depending on network)" -ForegroundColor DarkGray

    # Temporarily relax error preference (npm writes progress to stderr)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    $successCount = 0
    $failedPackages = @()
    foreach ($pkg in $npmPackages) {
        Write-Host "  Installing: $pkg" -ForegroundColor DarkGray
        # Use cmd /c to isolate stderr from PowerShell's error handling
        $null = cmd /c "docker exec $tempContainer npm install -g $pkg 2>nul"
        if ($LASTEXITCODE -eq 0) {
            $successCount++
        } else {
            $failedPackages += $pkg
            Write-Host "    FAILED: $pkg" -ForegroundColor Red
        }
    }
    $ErrorActionPreference = $prevEAP

    Write-Host "Installed: $successCount / $($npmPackages.Count) packages" -ForegroundColor Green
    if ($failedPackages.Count -gt 0) {
        Write-Host "Failed packages ($($failedPackages.Count)):" -ForegroundColor Yellow
        $failedPackages | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    }

    # Verify installation
    Write-Host "Installed global packages:" -ForegroundColor DarkGray
    docker exec $tempContainer npm ls -g --depth=0 2>$null | Select-Object -First 50

    # Export /usr/local/lib/node_modules as tarball
    Write-Host "Exporting global node_modules as tarball..." -ForegroundColor Yellow
    docker exec $tempContainer sh -c "tar -czf /tmp/$globalTarball -C /usr/local/lib node_modules"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: tar node_modules failed" -ForegroundColor Red
        exit 1
    }

    # Copy from container to host
    docker cp "${tempContainer}:/tmp/$globalTarball" $globalTarballPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: copy tarball from container failed" -ForegroundColor Red
        exit 1
    }

    $tarballSizeMB = [math]::Round((Get-Item $globalTarballPath).Length / 1MB, 2)
    Write-Host "Export done: $globalTarballPath ($tarballSizeMB MB)" -ForegroundColor Green
} finally {
    # Cleanup temp container
    Write-Host "Cleaning up temp container: $tempContainer" -ForegroundColor DarkGray
    docker rm -f $tempContainer 2>$null | Out-Null
}

Write-Host ""
Write-Host "========== Preparation complete ==========" -ForegroundColor Green
Write-Host "Node.js binary: $nodeTarballPath" -ForegroundColor White
Write-Host "npm global pkgs: $globalTarballPath" -ForegroundColor White
Write-Host ""
Write-Host "Next: run docker-build.qa.bat or docker-build.offline.sh to build containers" -ForegroundColor Cyan
