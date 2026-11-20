#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

"""Keeps config/jbenchrunner.sudoers in line with the commands jbenchrunner
runs as root (PRD.md D-87): prepare-for-benchmarks.sh, reset-machine.sh and
jbr-execute's shutdown and sudo checks. A command the scripts run differently
from its rule would fail with "a password is required" on a host that relies
on the rules. Run with:

    python3 -m unittest discover scripts
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PHASE_EXECUTE_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_PHASE_EXECUTE_DIR, ".."))
_REPOROOT = os.path.abspath(os.path.join(_SCRIPTS_DIR, ".."))
for _p in (_SCRIPTS_DIR, _PHASE_EXECUTE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import execute  # noqa: E402

TEMPLATE = os.path.join(_REPOROOT, "config", "jbenchrunner.sudoers")
STABILIZE = os.path.join(_PHASE_EXECUTE_DIR, "prepare-for-benchmarks.sh")
RESET = os.path.join(_PHASE_EXECUTE_DIR, "reset-machine.sh")

# Every per-CPU rule stands for CPUs 0-999, in one-character classes ('*'
# would match spaces and slashes, PRD.md D-87).
_CPU_CLASSES = ("cpu[0-9]/", "cpu[0-9][0-9]/", "cpu[0-9][0-9][0-9]/")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _aliases(lines):
    """Commands of the Cmnd_Alias definitions in *lines* (comments removed),
    unescaped, as full command lines ("/usr/sbin/sysctl kernel.x=0")."""
    logical, buf = [], ""
    for line in lines:
        if line.endswith("\\"):
            buf += line[:-1] + " "
        else:
            logical.append(buf + line)
            buf = ""
    commands = set()
    for line in logical:
        m = re.match(r"Cmnd_Alias\s+\w+\s*=\s*(.*)", line)
        if m:
            for part in re.split(r"(?<!\\),", m.group(1)):
                commands.add(re.sub(r"\\(.)", r"\1", " ".join(part.split())))
    return commands


def template_commands():
    """(active, commented_out) commands of the template, full paths."""
    active, commented = [], []
    for line in _read(TEMPLATE).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            content = stripped.lstrip("#").strip()
            if content.startswith(("Cmnd_Alias", "/usr/")):
                commented.append(content)
        else:
            active.append(stripped)
    return _aliases(active), _aliases(commented)


def _short(command):
    """'/usr/sbin/sysctl kernel.x=0' -> 'sysctl kernel.x=0': the scripts call
    the tools by name; sudo resolves them through its secure_path."""
    tool, _, args = command.partition(" ")
    return f"{os.path.basename(tool)} {args}".strip()


def _code_lines(path):
    return [line for line in _read(path).splitlines()
            if line.strip() and not line.strip().startswith("#")]


def script_commands():
    """The root commands of prepare-for-benchmarks.sh and reset-machine.sh,
    by tool name ("sysctl kernel.x=0", "tee /proc/sys/vm/drop_caches")."""
    commands = set()
    for path in (STABILIZE, RESET):
        for line in _code_lines(path):
            # direct calls with fixed arguments: sudo -n <tool> <args>
            for m in re.finditer(r"\bsudo -n (?!-l\b)(.+?)\s*(?:\|\||>|\\$|$)", line):
                if "$" not in m.group(1):
                    commands.add(m.group(1))
            # sudo_write <value> <fixed path>  ->  tee <path>
            m = re.search(r"\bsudo_write \S+ (/\S+)", line)
            if m:
                commands.add(f"tee {m.group(1)}")
            # write_all "<what>" "<value>" "<per-CPU glob>"  ->  tee per CPU
            m = re.search(r'\bwrite_all "[^"]*" "[^"]*" "([^"]+)"', line)
            if m:
                assert "cpu*/" in m.group(1), m.group(1)
                for cls in _CPU_CLASSES:
                    commands.add("tee " + m.group(1).replace("cpu*/", cls))
            # stop_if_active <service>  ->  systemctl stop <service>
            m = re.match(r"\s*stop_if_active (\S+)$", line)
            if m:
                commands.add(f"systemctl stop {m.group(1)}")
    return commands


def sudo_list_checks():
    """Full-path commands checked with 'sudo -n -l' by the scripts and
    jbr-execute."""
    checks = {" ".join(execute._SUDO_CHECK_STABILIZE),
              " ".join(execute._SUDO_CHECK_SHUTDOWN)}
    for path in (STABILIZE, RESET):
        for line in _code_lines(path):
            m = re.search(r"\bsudo -n -l (/\S+(?: [^>]*?)?)\s*>", line)
            if m:
                checks.add(m.group(1))
    return checks


class TestSudoersTemplate(unittest.TestCase):

    def setUp(self):
        self.active, self.commented = template_commands()

    def test_template_parses(self):
        # sanity check of the parser itself
        self.assertIn("/usr/sbin/sysctl kernel.perf_event_paranoid=0", self.active)
        self.assertIn("/usr/sbin/shutdown -h now", self.active)

    def test_rules_match_script_commands_exactly(self):
        # Every root command of the scripts has its rule, and every rule is
        # used: commented-out ones included (FIX_CPU_FREQUENCY).
        rules = {_short(c) for c in self.active | self.commented}
        expected = script_commands() | {" ".join(execute._SHUTDOWN_CMD)}
        self.assertEqual(sorted(expected - rules), [], "commands without a rule")
        self.assertEqual(sorted(rules - expected), [], "rules no command uses")

    def test_commented_out_rules_are_only_the_frequency_cap(self):
        self.assertTrue(self.commented)
        for command in self.commented:
            self.assertIn("/scaling_max_freq", command)

    def test_sudo_checks_name_granted_commands(self):
        for check in sudo_list_checks():
            with self.subTest(check=check):
                self.assertIn(check, self.active)

    def test_full_absolute_paths(self):
        for command in self.active | self.commented:
            with self.subTest(command=command):
                self.assertTrue(command.startswith("/usr/"), command)

    def test_rules_granted_to_group_jbenchrunners(self):
        # The group name the docs and error messages tell users to join.
        grants = [line for line in _read(TEMPLATE).splitlines()
                  if line.strip() and not line.strip().startswith("#")
                  and "NOPASSWD:" in line]
        self.assertEqual(len(grants), 1)
        self.assertTrue(grants[0].startswith("%jbenchrunners ALL=(root) NOPASSWD: "),
                        grants[0])

    def test_no_wildcard_star(self):
        for line in _read(TEMPLATE).splitlines():
            if not line.strip().startswith("#"):
                self.assertNotIn("*", line)


if __name__ == "__main__":
    unittest.main()
