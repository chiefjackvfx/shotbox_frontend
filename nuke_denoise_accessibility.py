"""Read Windows dialog labels across toolkit versions, without invoking controls."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import time

from nuke_denoise_display import DenoiseCancelled


# Windows PowerShell ships the .NET UI Automation client. Run it on this job's
# private desktop in an owned process: provider calls can hang, so an in-process
# COM call or Python thread would make cancellation/cleanup unbounded.
QUERY = r"""
$ErrorActionPreference = 'Stop'
$report = @{ handle = $targetHandle; available = $false; elements = @() }
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$targetHandle)
    if ($null -eq $root) { throw 'No accessibility provider for this window.' }
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    $items = New-Object 'System.Collections.Generic.List[object]'
    function Read-Element($element, [int]$depth) {
        if ($items.Count -ge 64 -or $depth -gt 6) { return }
        $current = $element.Current
        $name = [string]$current.Name
        $help = [string]$current.HelpText
        $items.Add(@{
            name = $name.Substring(0, [Math]::Min(4096, $name.Length))
            help = $help.Substring(0, [Math]::Min(4096, $help.Length))
            class = $current.ClassName
            type = $current.ControlType.ProgrammaticName
            enabled = $current.IsEnabled
            depth = $depth
        })
        if ($depth -ge 6) { return }
        $child = $walker.GetFirstChild($element)
        while ($null -ne $child -and $items.Count -lt 64) {
            Read-Element $child ($depth + 1)
            $child = $walker.GetNextSibling($child)
        }
    }
    Read-Element $root 0
    $report.elements = @($items.ToArray())
    $report.available = $true
} catch {
    $report.error = $_.Exception.Message
}
$json = ConvertTo-Json -InputObject $report -Depth 6
[System.IO.File]::WriteAllText($destination, $json, [System.Text.Encoding]::UTF8)
"""


def query_command(hwnd, destination):
    executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # Single-quoted PowerShell strings escape apostrophes by doubling them.
    # EncodedCommand preserves Unicode paths without a shell or interpolation.
    quoted = str(destination).replace("'", "''")
    script = f"$targetHandle = [long]{int(hwnd)}\n$destination = '{quoted}'\n" + QUERY
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-MTA", "-EncodedCommand", encoded]


def read_dialog(display, hwnd):
    """Bound the complete query; keep the helper in the existing Windows job."""
    path = display.directory / "dialog-accessibility.json"
    try:
        display._check()
        if hwnd not in {window for window, _ in display.windows()}:
            raise RuntimeError("Dialog left the private desktop before accessibility inspection.")
        path.unlink(missing_ok=True)
        command = query_command(hwnd, path)
        with (display.directory / "dialog-accessibility.log").open("wb") as log:
            process = display.api.create_process(command, display.number, display.job,
                                                  display.environment(dict(os.environ)), str(display.directory), log)
            try:
                process.resume()
                deadline = time.monotonic() + 4
                while process.poll() is None:
                    display._check()
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Windows accessibility query timed out after four seconds.")
                    try:
                        process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode != 0:
                    raise RuntimeError(f"Windows accessibility helper exited with code {process.returncode}; see dialog-accessibility.log.")
            finally:
                try:
                    if process.poll() is None:
                        process.terminate()
                finally:
                    process.close()
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        if result.get("handle") != int(hwnd):
            raise RuntimeError("Windows accessibility response did not match the dialog.")
        return result
    except DenoiseCancelled:
        raise
    except Exception as error:
        return {"available": False, "error": str(error)}
