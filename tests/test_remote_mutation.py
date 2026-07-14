from __future__ import annotations

from core.shell_command import (
    ShellEffect,
    classify_shell_effect,
    is_remote_mutation_command,
    is_remote_only_mutation_command,
)


def _isolated_remote(command: str) -> str:
    executable, separator, arguments = command.partition(" ")
    assert separator
    return (
        f"{executable} -F none -o StrictHostKeyChecking=yes {arguments}"
    )


def test_direct_ssh_and_scp_upload_are_remote_only_mutations() -> None:
    commands = (
        'ssh deploy@example.com "sudo systemctl restart app"',
        "ssh -p 2222 deploy@example.com uptime",
        "scp artifact.tar deploy@example.com:/srv/app/",
        "scp -P 2222 -r dist deploy@example.com:/srv/app/",
    )

    for command in commands:
        isolated = _isolated_remote(command)
        assert is_remote_only_mutation_command(isolated) is True, isolated


def test_remote_commands_with_identity_and_attached_port_options_stay_remote_only() -> None:
    commands = (
        'ssh -i deploy-key deploy@example.com "touch remote-marker"',
        'ssh -J bastion deploy@example.com "touch remote-marker"',
        "scp -i deploy-key artifact.tar deploy@example.com:/srv/app/",
        "scp -J bastion artifact.tar deploy@example.com:/srv/app/",
        "scp -o BatchMode=yes artifact.tar deploy@example.com:/srv/app/",
        'ssh -p2222 deploy@example.com "touch remote-marker"',
        "scp -P2222 artifact.tar deploy@example.com:/srv/app/",
    )

    # Given/When/Then: safe option values are consumed before remote operands are classified.
    assert tuple(
        is_remote_only_mutation_command(_isolated_remote(command))
        for command in commands
    ) == (
        True,
        True,
        True,
        True,
        True,
        True,
        True,
    )


def test_remote_commands_with_combined_safe_flags_stay_remote_only() -> None:
    commands = (
        'ssh -qT deploy@example.com "touch remote-marker"',
        "scp -pr dist deploy@example.com:/srv/app/",
    )

    for command in commands:
        isolated = _isolated_remote(command)
        assert is_remote_only_mutation_command(isolated) is True, isolated


def test_supported_read_only_openssh_options_do_not_hide_remote_mutations() -> None:
    commands = (
        'ssh -ACKksXxY deploy@example.com "touch remote-marker"',
        'ssh -B Ethernet -b 127.0.0.1 -c aes128-ctr deploy@example.com "touch remote-marker"',
        'ssh -e none -m hmac-sha2-256 deploy@example.com "touch remote-marker"',
        'ssh -D 1080 -L 8080:localhost:80 -R 9090:localhost:90 deploy@example.com "touch remote-marker"',
        "scp -3ABCOqT artifact.tar deploy@example.com:/srv/app/",
        "scp -c aes128-ctr -l 1000 -X nrequests=64 artifact.tar deploy@example.com:/srv/app/",
    )

    for command in commands:
        isolated = _isolated_remote(command)
        assert is_remote_only_mutation_command(isolated) is True, isolated


def test_read_only_ssh_config_options_do_not_hide_remote_mutations() -> None:
    commands = (
        'ssh -o Compression=yes -o Ciphers=aes128-ctr -o Port=2222 -o User=deploy host "touch remote-marker"',
        "scp -o Compression=yes -o Ciphers=aes128-ctr -o Port=2222 -o User=deploy artifact.tar host:/srv/app/",
        'ssh -o StrictHostKeyChecking=yes host "touch remote-marker"',
    )

    for command in commands:
        isolated = _isolated_remote(command)
        assert is_remote_only_mutation_command(isolated) is True, isolated


def test_remote_mutation_epoch_is_independent_of_local_effect_classification() -> None:
    commands = (
        'ssh -F none -o StrictHostKeyChecking=yes -f deploy@example.com "touch marker"',
        'ssh -o KexAlgorithms=curve25519-sha256 host "touch remote-marker"',
        'ssh -o TCPKeepAlive=yes host "touch remote-marker"',
        'ssh -o PubkeyAuthentication=yes host "touch remote-marker"',
        'ssh -o PasswordAuthentication=no host "touch remote-marker"',
        'ssh -o AddressFamily=inet host "touch remote-marker"',
        'ssh -o "RemoteCommand=touch /tmp/marker" host',
        'ssh -E local.log host "touch remote-marker"',
        'ssh -o ControlPath=local.sock host "touch remote-marker"',
        "scp -F ssh-config artifact.tar host:/srv/app/",
        "scp artifact.tar host:/srv/app/ > transfer.log",
        'ssh -vp2222 host "touch remote-marker"',
        'ssh -qJbastion host "touch remote-marker"',
        'ssh -Tiidentity host "touch remote-marker"',
        "scp artifact.tar host:/srv/app/ 1>transfer.log",
        "scp artifact.tar host:/srv/app/ 2>transfer.err",
        "scp artifact.tar host:/srv/app/ 2>>transfer.err",
        "scp artifact.tar host:/srv/app/ 2>&1",
        "scp artifact.tar host:/srv/app/ |& tee transfer.log",
        "scp 2>transfer.err artifact.tar host:/srv/app/",
        "2>transfer.err scp artifact.tar host:/srv/app/",
        '2>ssh.err ssh host "touch remote-marker"',
    )

    for command in commands:
        assert is_remote_mutation_command(command) is True, command


def test_env_wrapped_remote_mutation_creates_epoch() -> None:
    command = 'env FOO=bar ssh deploy@host "touch /srv/marker"'
    result = is_remote_mutation_command(command)
    assert result is True


def test_bash_wrapped_remote_mutation_creates_epoch() -> None:
    command = 'bash -c "ssh deploy@host touch /srv/marker"'
    result = is_remote_mutation_command(command)
    assert result is True


def test_sh_wrapped_remote_mutation_creates_epoch() -> None:
    command = 'sh -c "ssh deploy@host touch /srv/marker"'
    result = is_remote_mutation_command(command)
    assert result is True


def test_uv_wrapped_remote_mutation_creates_epoch() -> None:
    command = 'uv run ssh deploy@host "touch /srv/marker"'
    result = is_remote_mutation_command(command)
    assert result is True


def test_non_remote_ssh_and_scp_operations_do_not_create_remote_epochs() -> None:
    commands = (
        "echo ssh host touch /tmp/x",
        "ssh -G host",
        "ssh -N host",
        "ssh -O check host",
        "ssh -Q cipher",
        "ssh -V",
        "ssh -W target.example.com:22 host",
        "ssh -o SessionType=none host",
        "scp host:/tmp/a ./a",
    )

    for command in commands:
        assert is_remote_mutation_command(command) is False, command


def test_remote_commands_with_local_or_mixed_effects_are_not_remote_only() -> None:
    commands = (
        "ssh deploy@example.com uptime > local.log",
        "ssh deploy@example.com uptime | tee local.log",
        "scp deploy@example.com:/tmp/a ./a",
        "scp local.txt deploy@example.com:/tmp/a && rm local.txt",
        "ssh deploy@example.com $(cat local.txt)",
        "ssh deploy@example.com `cat local.txt`",
        "ssh deploy@example.com uptime\nprintf done",
        "ssh -E local.log deploy@example.com uptime",
        'ssh -I ./provider.dll deploy@example.com "touch remote-marker"',
        'ssh -L ./local.sock:remote.sock deploy@example.com "touch remote-marker"',
        "ssh -S local.sock deploy@example.com uptime",
        "ssh -M deploy@example.com uptime",
        "ssh -O check deploy@example.com",
        'ssh -P release deploy@example.com "touch remote-marker"',
        "ssh -G deploy@example.com",
        "ssh -N deploy@example.com",
        "ssh -Q cipher",
        "ssh -V",
        "ssh -W target.example.com:22 deploy@example.com",
        'ssh -y deploy@example.com "touch remote-marker"',
        "ssh -o LocalCommand=touch-local deploy@example.com uptime",
        "ssh -o ProxyCommand=local-proxy deploy@example.com uptime",
        (
            'ssh -F none -o StrictHostKeyChecking=yes '
            '-o "Hostname localhost" remotealias "touch marker"'
        ),
        (
            'ssh -F none -o StrictHostKeyChecking=yes '
            '-o "ProxyJump localhost" remotealias "touch marker"'
        ),
        "ssh -o ControlPath=local.sock deploy@example.com uptime",
        'ssh -o StrictHostKeyChecking=accept-new host "touch remote-marker"',
        'ssh -o StrictHostKeyChecking=ask host "touch remote-marker"',
        'ssh -o StrictHostKeyChecking=no host "touch remote-marker"',
        'ssh -o StrictHostKeyChecking=off host "touch remote-marker"',
        (
            "ssh -o StrictHostKeyChecking=accept-new "
            "-o UserKnownHostsFile=./local-known-hosts deploy@example.com uptime"
        ),
        "scp -D local-sftp artifact.tar deploy@example.com:/srv/app/",
        "scp -F ssh-config artifact.tar deploy@example.com:/srv/app/",
        "scp -S ssh-wrapper artifact.tar deploy@example.com:/srv/app/",
    )

    for command in commands:
        assert is_remote_only_mutation_command(command) is False, command


def test_shell_effect_defaults_conservatively_and_preserves_proven_remote_targets() -> None:
    cases = (
        ("git status --short", ShellEffect.LOCAL_OR_UNKNOWN, ()),
        ("git rev-parse --show-toplevel", ShellEffect.PROVEN_READ_ONLY, ()),
        ("rg --no-config provenance core", ShellEffect.PROVEN_READ_ONLY, ()),
        (
            'ssh -F none -o StrictHostKeyChecking=yes deploy@example.com "touch /srv/marker"',
            ShellEffect.PROVEN_REMOTE_ONLY,
            ("ssh://deploy@example.com:22",),
        ),
        (
            "scp -F none -o StrictHostKeyChecking=yes -P 2222 artifact.tar deploy@example.com:/srv/app/",
            ShellEffect.PROVEN_REMOTE_ONLY,
            ("ssh://deploy@example.com:2222",),
        ),
        ("git status --short > status.txt", ShellEffect.LOCAL_OR_UNKNOWN, ()),
        ("scp example.com:/tmp/a ./a", ShellEffect.LOCAL_OR_UNKNOWN, ()),
        (
            'ssh deploy@example.com "touch /srv/marker" && rm local.txt',
            ShellEffect.LOCAL_OR_UNKNOWN,
            ("ssh://deploy@example.com:22",),
        ),
        (
            'bash -c "ssh deploy@example.com touch /srv/marker"',
            ShellEffect.LOCAL_OR_UNKNOWN,
            ("ssh://deploy@example.com:22",),
        ),
        ("opaque-shell-writer", ShellEffect.LOCAL_OR_UNKNOWN, ()),
        ('ssh deploy@example.com "touch marker"', ShellEffect.LOCAL_OR_UNKNOWN, ("ssh://deploy@example.com:22",)),
    )

    for command, effect, target_ids in cases:
        classification = classify_shell_effect(command)
        assert classification.effect is effect, command
        assert classification.remote_target_ids == target_ids, command


def test_remote_target_identity_distinguishes_user_host_and_port() -> None:
    commands = (
        'ssh host "touch marker"',
        'ssh deploy@host "touch marker"',
        'ssh -p 2222 deploy@host "touch marker"',
        'ssh deploy@other "touch marker"',
    )

    target_ids = tuple(
        classify_shell_effect(command).remote_target_ids[0] for command in commands
    )

    assert target_ids == (
        "ssh://host:22",
        "ssh://deploy@host:22",
        "ssh://deploy@host:2222",
        "ssh://deploy@other:22",
    )
    assert len(set(target_ids)) == len(target_ids)


def test_jump_host_and_localhost_do_not_gain_local_provenance_authority() -> None:
    jump = classify_shell_effect('ssh -J bastion deploy@host "touch marker"')
    localhost = classify_shell_effect('ssh localhost "echo hi"')
    ipv4_loopback = classify_shell_effect('ssh 127.0.0.1 "touch marker"')
    ipv6_loopback = classify_shell_effect('ssh ::1 "touch marker"')

    assert jump.effect is ShellEffect.LOCAL_OR_UNKNOWN
    assert jump.remote_target_ids == ("ssh://deploy@host:22",)
    assert localhost.effect is ShellEffect.LOCAL_OR_UNKNOWN
    assert localhost.remote_target_ids == ("ssh://localhost:22",)
    assert ipv4_loopback.effect is ShellEffect.LOCAL_OR_UNKNOWN
    assert ipv6_loopback.effect is ShellEffect.LOCAL_OR_UNKNOWN


def test_read_only_allowlist_rejects_external_program_escape_hatches() -> None:
    cases = (
        ("./rg --no-config needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("C:/tmp/rg.exe --no-config needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("./git status --short", ShellEffect.LOCAL_OR_UNKNOWN),
        (
            "C:/tmp/git.exe diff --no-ext-diff --no-textconv",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
        ("git diff", ShellEffect.LOCAL_OR_UNKNOWN),
        ("git diff --ext-diff", ShellEffect.LOCAL_OR_UNKNOWN),
        (
            "git diff --no-ext-diff --no-textconv --ext-diff",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
        (
            "git show --no-ext-diff --no-textconv --textconv HEAD:file.py",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
        (
            "git log --no-ext-diff --no-textconv --output=owned.txt",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
        ("git show --textconv HEAD:file.py", ShellEffect.LOCAL_OR_UNKNOWN),
        (
            "GIT_EXTERNAL_DIFF=opaque-writer git diff --no-ext-diff --no-textconv",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
        ("rg needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("rg --pre opaque-writer needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("rg --no-config -z needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("rg --no-config -zU needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("rg --no-config -Uz needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("rg --no-config -zz needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        ("rg --no-config --search-zip needle .", ShellEffect.LOCAL_OR_UNKNOWN),
        (
            "rg --no-config --hostname-bin opaque-writer "
            "--hyperlink-format=file://{host}{path} needle .",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
        (
            "rg --no-config --hostname-bin=opaque-writer needle .",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
        ("rg --no-config needle .", ShellEffect.PROVEN_READ_ONLY),
        (
            "git diff --no-ext-diff --no-textconv",
            ShellEffect.LOCAL_OR_UNKNOWN,
        ),
    )

    for command, expected in cases:
        assert classify_shell_effect(command).effect is expected, command


def test_path_qualified_remote_executables_never_gain_remote_only_authority() -> None:
    commands = (
        './ssh deploy@host "touch marker"',
        'C:/tmp/ssh.exe deploy@host "touch marker"',
        "./scp artifact.tar deploy@host:/srv/app/",
        "C:/tmp/scp.exe artifact.tar deploy@host:/srv/app/",
    )

    for command in commands:
        classification = classify_shell_effect(command)
        assert classification.effect is ShellEffect.LOCAL_OR_UNKNOWN, command
        assert classification.remote_target_ids == (), command
