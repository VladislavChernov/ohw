# Check: every project-code service is mounted, external tools are not.
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

# External tools: our code never runs inside them.
$external = @('neo4j', 'valkey', 'llm', 'reranker')

$combos = @(
    @{ files = @('compose.yaml') },
    @{ files = @('compose.yaml', 'compose.eval-minimal.yaml') },
    @{ files = @('compose.runtime.yaml') },
    @{ files = @('compose.preingest.yaml') },
    @{ files = @('compose.yaml', 'compose.eval-minimal.yaml', 'compose.eval-qwen3b.yaml') }
)

$problems = @()
$phantoms = @()
$checked = 0

foreach ($combo in $combos) {
    $argv = @('compose')
    foreach ($f in $combo.files) { $argv += @('--file', $f) }
    $argv += @('--profile', '*', 'config', '--format', 'json')

    $raw = & docker @argv 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        $problems += "$($combo.files -join ' + '): INVALID"
        continue
    }

    $config = $raw | ConvertFrom-Json
    $label = $combo.files -join ' + '
    $props = @($config.services.PSObject.Properties)

    # All services sit behind profiles, so without `--profile *` compose resolves an
    # EMPTY service map - and a check over zero services reports PASS. That is the
    # silent-zero trap, and it already produced one false pass here.
    if ($props.Count -eq 0) {
        $problems += "$label : resolved ZERO services - check is vacuous"
        continue
    }

    foreach ($prop in $props) {
        $name = $prop.Name
        $spec = $prop.Value
        $checked++

        $mounted = $false
        if ($spec.volumes) {
            foreach ($v in $spec.volumes) {
                $text = if ($v -is [string]) { $v } else { "$($v.source):$($v.target)" }
                if ($text -match '/app/src') { $mounted = $true }
            }
        }

        if ($external -contains $name) {
            if ($mounted) { $problems += "$label/$name : mount leaked into external tool" }
            continue
        }

        # No `build:` means the image cannot be produced from this project. Such a service is
        # reported separately: it is NOT automatically a defect, because a service may be
        # declared on purpose and deferred (configurator-ui is documented in docs/04 and
        # kept in compose by owner decision). It must still be reported every run, so it
        # cannot quietly rot into something nobody looked at.
        $imageName = "$($spec.image)"
        $imageExists = if ($imageName) { [bool](docker images -q $imageName) } else { $false }

        if (-not $spec.build -and -not $imageExists) {
            $phantoms += "$label/$name : image '$imageName' not built, no build context (declared, not runnable)"
            continue
        }

        if (-not $mounted) { $problems += "$label/$name : NO mount" }
    }
}

Write-Output "services checked: $checked"
if ($phantoms.Count -gt 0) {
    Write-Output "DECLARED BUT NOT RUNNABLE (not necessarily a defect - verify intent):"
    $phantoms | Sort-Object -Unique | ForEach-Object { Write-Output "  $_" }
}
if ($problems.Count -gt 0) {
    Write-Output "PROBLEMS:"
    $problems | ForEach-Object { Write-Output "  $_" }
    exit 1
}
Write-Output "PASS: project services mounted, external tools untouched."
exit 0