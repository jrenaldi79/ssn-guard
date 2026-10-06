"""Run Windows commands with output withheld until masking succeeds."""
import base64
import os
import subprocess
import sys

from ssn_mask import mask_text, notice


def wrap_windows_command(command: str) -> str:
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    def quote(value):
        return "'" + value.replace("'", "''") + "'"
    runner = os.path.abspath(__file__)
    return ("# ssn-guard:wrapped\n"
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
            "$OutputEncoding = [Console]::OutputEncoding\n& ") + " ".join(
        quote(value) for value in (sys.executable, runner, encoded)
    ) + " (Join-Path $PSHOME $(if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }))\nexit $LASTEXITCODE"


def main() -> int:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        command = base64.b64decode(sys.argv[1], validate=True).decode("utf-8")
        shell = sys.argv[2]
        if os.path.basename(shell).lower() not in ("powershell.exe", "pwsh.exe"):
            raise ValueError("unsupported shell")
        # Record command status before masking runs, including native exit codes.
        script = (
            "$ProgressPreference = 'SilentlyContinue'\n"
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
            "$OutputEncoding = [Console]::OutputEncoding\n"
            "$global:LASTEXITCODE = 0\n"
            "$script:ssng_ok = $true\n"
            "& {\n" + command + "\n$script:ssng_ok = $?\n} | Out-Default\n"
            "if (-not $ssng_ok -and $LASTEXITCODE -eq 0) { exit 1 }\n"
            "exit $LASTEXITCODE\n"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        masked, count = mask_text(output)
        sys.stdout.write(masked)
        if count:
            sys.stderr.write(notice(count) + "\n")
        return result.returncode
    except Exception:
        # Never include command output or exception details in a failure message.
        sys.stderr.write("ssn-guard: command execution or masking failed; output withheld\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
