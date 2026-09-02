# Builds the shared python base image used by homework app Dockerfiles.
# Usage (from anywhere):  powershell -File D:\Otus\infra\python\build.ps1
param(
    [switch]$NoCache
)

$image = "ohw-python:3.13"
$dir = $PSScriptRoot

Write-Host "Building $image from $dir ..."
if ($NoCache) {
    docker build --no-cache -t $image $dir
} else {
    docker build -t $image $dir
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "build failed"
    exit 1
}
Write-Host "OK: $image"
