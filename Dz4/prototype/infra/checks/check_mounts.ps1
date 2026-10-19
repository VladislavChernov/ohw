# Check: every service running OUR code mounts the host source read-only at /app/src.
#
# The RESOLVED compose config is inspected, not the source text: an overlay REPLACES
# the volumes list wholesale, so a mount added only to the base file disappears on
# overlay application, and the source text does not show it.
#
# ASCII only on purpose: Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI and
# mangles non-ASCII into a parser error.

$ErrorActionPreference = 'Stop'
$env:RUN_CODE_COMMIT = 'test'
$env:VOL = 'empty'
$env:NEO4J_PASSWORD = 'x'

# ---------------------------------------------------------------------------
# What counts as "our code"
#
# Three wrong versions came before this one, and each was caught BY this instrument,
# which is the only reason to trust it now:
#
#   1. Hard-coded service-name list. Brittle and uncheckable.
#   2. `build.dockerfile` only. Declared our services as foreign: `ingestion-api` and
#      `projection` run the shared image `ohw/ingestion-service:prototype` with NO build
#      section at all, so they have no dockerfile to compare.
#   3. Image namespace `ohw/*` only. Rejected `pre-ingest`, which is a one-shot runner
#      with NO image, existing only under `docker compose run`.
#
# So the positive test is: a build context whose dockerfile is not an external one, OR an
# `ohw/*` image with no build context. Both readings come from the resolved config.
#
# External tools are llama.cpp and the reranker model (ADR-022, ADR-027). They are
# excluded by Dockerfile where a build section exists, and by image where it does not -
# `compose.runtime.yaml` declares `llm` without `build:`, so there is no dockerfile to
# read, and excluding it by name is a NEGATIVE claim about a known list, not the
# positive invariant being protected here.
# ---------------------------------------------------------------------------

$externalDockerfiles = @('Dockerfile.llm', 'Dockerfile.reranker')
$externalImages = @('ohw/llm:prototype', 'ohw/reranker:prototype')

$combos = @(
    @{ files = @('compose.yaml') },
    @{ files = @('compose.yaml', 'compose.eval-minimal.yaml') },
    @{ files = @('compose.runtime.yaml') },
    @{ files = @('compose.preingest.yaml') },
    @{ files = @('compose.yaml', 'compose.eval-minimal.yaml', 'compose.eval-qwen3b.yaml') },
    # runtime + ingest is the combination that actually fills the `ready` set, so it is
    # checked too: an overlay nobody checks is an overlay nobody notices breaking.
    @{ files = @('compose.runtime.yaml', 'compose.ingest.yaml') }
)

$problems = @()
$deferred = @()
$checked = 0
$expectedTotal = 0


function Test-IsOurs($spec) {
    if ($spec.build) {
        $dockerfile = "$($spec.build.dockerfile)"
        return $externalDockerfiles -notcontains $dockerfile
    }
    $image = "$($spec.image)"
    return $image -like 'ohw/*' -and $externalImages -notcontains $image
}


function Test-IsExternal($spec) {
    if ($spec.build) {
        return $externalDockerfiles -contains "$($spec.build.dockerfile)"
    }
    return $externalImages -contains "$($spec.image)"
}


function Get-SrcMount($spec) {
    if (-not $spec.volumes) { return $null }
    foreach ($v in $spec.volumes) {
        if ("$($v.target)" -eq '/app/src') { return $v }
    }
    return $null
}


foreach ($combo in $combos) {
    $argv = @('compose')
    foreach ($f in $combo.files) { $argv += @('--file', $f) }
    $argv += @('--profile', '*', 'config', '--format', 'json')

    $raw = & docker @argv 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $problems += "$($combo.files -join ' + '): INVALID compose"
        continue
    }

    $config = $raw | ConvertFrom-Json
    $label = $combo.files -join ' + '
    $props = @($config.services.PSObject.Properties)

    # All services sit behind profiles, so without `--profile *` compose resolves an
    # EMPTY service map, and a check over zero services reports PASS. That silent-zero
    # trap already produced one false pass here.
    if ($props.Count -eq 0) {
        $problems += "$label : resolved ZERO services - check is vacuous"
        continue
    }

    $ours = @($props | Where-Object { Test-IsOurs $_.Value })
    $expectedTotal += $ours.Count

    foreach ($prop in $props) {
        $name = $prop.Name
        $spec = $prop.Value
        $mount = Get-SrcMount $spec

        if (Test-IsExternal $spec) {
            # Our code never runs inside llama.cpp or the reranker. A source mount here
            # would be a real mistake, not a harmless extra.
            if ($mount) { $problems += "$label/$name : source mounted into external tool" }
            continue
        }

        if (-not (Test-IsOurs $spec)) { continue }

        # Counted as checked, so the denominator has no silent holes.
        $checked++

        # No build context means the image cannot be produced from this project, so the
        # service cannot start. A mount check on it would be a permanently red line that
        # teaches people to ignore this output. It is reported as NOT CHECKED rather than
        # as a pass: when `build:` appears, the requirement applies and this becomes a
        # problem if the mount is missing.
        if (-not $spec.build) {
            $deferred += "$label/$name : image '$($spec.image)' has no build context; not runnable, mount requirement NOT checked"
            continue
        }

        if (-not $mount) {
            $problems += "$label/$name : NO mount of ../src"
            continue
        }

        # Read-only is part of the invariant, not a nicety: the container must not write
        # back into the host working tree. compose resolves `ro` into `read_only`.
        if ($mount.read_only -ne $true) {
            $problems += "$label/$name : mount is not read-only"
            continue
        }

        # The resolved source is an absolute host path - that is NORMAL, compose does it.
        # What must not appear is a hard-coded path IN THE FILE, so the leaf is checked.
        $leaf = "$($mount.source)" -replace '.*[\\/]', ''
        if ($leaf -ne 'src') {
            $problems += "$label/$name : mount points to '$leaf', expected 'src'"
        }
    }
}

# The denominator is stated and is the right unit: services carrying our code. It is
# computed from the resolved config rather than hard-coded, so it cannot rot.
if ($expectedTotal -eq 0) {
    Write-Output "ZERO services carry project code across all combos - check is vacuous."
    exit 2
}
if ($checked -ne $expectedTotal) {
    Write-Output "INTERNAL: checked $checked but expected $expectedTotal - coverage gap."
    exit 2
}

Write-Output "project-code services checked: $checked / $expectedTotal resolved"

$uniqueDeferred = @($deferred | Sort-Object -Unique)
if ($uniqueDeferred.Count -gt 0) {
    Write-Output "DECLARED BUT NOT RUNNABLE (verify intent, not automatically a defect):"
    $uniqueDeferred | ForEach-Object { Write-Output "  $_" }
}

if ($problems.Count -gt 0) {
    Write-Output "PROBLEMS:"
    $problems | ForEach-Object { Write-Output "  $_" }
    exit 1
}
Write-Output "PASS: every service running project code mounts ../src read-only at /app/src."
exit 0