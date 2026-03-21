# Shell Script Test Setup

## Prerequisites

Install BATS (Bash Automated Testing System):

```bash
# Ubuntu/Debian
sudo apt install bats

# Or from source
git clone https://github.com/bats-core/bats-core.git
cd bats-core && sudo ./install.sh /usr/local
```

## Running the tests

```bash
# Run all shell tests
bats tests_shell/

# Run a single file
bats tests_shell/test_install.bats
```

## Notes

- Tests use stub commands to avoid requiring real nvidia-smi / ipmitool / systemctl.
- Tests run in a temporary directory and do NOT modify the real filesystem.
- Root-check tests verify that the script exits correctly for non-root users.
