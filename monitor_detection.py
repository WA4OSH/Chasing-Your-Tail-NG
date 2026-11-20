import re
import subprocess


def get_monitor_interfaces():
    """Return names of Linux wireless interfaces in monitor mode."""
    try:
        result = subprocess.run(
            ["iw", "dev"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    interfaces = []
    current_interface = None

    for line in result.stdout.splitlines():
        match = re.match(r"^\s*Interface\s+(\S+)", line)
        if match:
            current_interface = match.group(1)
        elif current_interface and re.match(r"^\s*type\s+monitor\s*$", line):
            interfaces.append(current_interface)

    return interfaces
