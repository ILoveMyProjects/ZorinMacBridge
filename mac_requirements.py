from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SetupRequirement:
    key: str
    label: str
    impact: str
    ready: bool


def evaluate_setup_requirements(
    *,
    screen_recording: bool | None,
    accessibility: bool | None,
    session_password_configured: bool,
    admin_state: Mapping[str, object] | None,
    launch_at_login: bool,
    auto_start_server: bool,
) -> list[SetupRequirement]:
    """Return requirements for normal logged-in Aqua operation.

    LoginWindow/pre-login verification is intentionally separate so an
    unverified reboot-login path cannot make an otherwise working Aqua host
    appear globally NOT READY.
    """
    requirements = [
        SetupRequirement(
            'screen_recording',
            'Screen Recording permission',
            'required for remote video',
            screen_recording is True,
        ),
        SetupRequirement(
            'accessibility',
            'Accessibility permission',
            'required for remote mouse and keyboard control',
            accessibility is True,
        ),
        SetupRequirement(
            'session_password',
            'ZorinMacBridge session password',
            'required for Desktop authentication',
            bool(session_password_configured),
        ),
    ]

    if admin_state is None:
        requirements.append(
            SetupRequirement(
                'p0_status',
                'P0 system status',
                'required to verify unattended recovery and SSH readiness',
                False,
            )
        )
    else:
        installed = bool(admin_state.get('installed'))
        requirements.append(
            SetupRequirement(
                'p0_installed',
                'P0 system helper',
                'required for unattended recovery and System actions',
                installed,
            )
        )
        if installed:
            requirements.extend([
                SetupRequirement(
                    'p0_daemon',
                    'P0 recovery daemon',
                    'required for unattended recovery and system discovery',
                    bool(admin_state.get('daemon_loaded')),
                ),
                SetupRequirement(
                    'update_daemon',
                    'P0 update service',
                    'required for unattended update checks/repair',
                    bool(admin_state.get('updater_loaded')),
                ),
            ])

        remote_login = str(admin_state.get('remote_login') or 'unknown')
        requirements.append(
            SetupRequirement(
                'remote_login',
                'macOS Remote Login (SSH)',
                'required for pairing, System, Terminal and Project actions',
                remote_login == 'on',
            )
        )
        if remote_login == 'on':
            requirements.append(
                SetupRequirement(
                    'ssh_transport',
                    'SSH transport on TCP 22',
                    'required for Zorin-to-Mac administration',
                    bool(admin_state.get('ssh_port_ready')),
                )
            )
        requirements.append(
            SetupRequirement(
                'ssh_host_key',
                'macOS SSH host public key',
                'required for fingerprint verification during pairing',
                bool(admin_state.get('ssh_host_key_ready')),
            )
        )

    requirements.extend([
        SetupRequirement(
            'launch_at_login',
            'Launch ZorinMacBridge at login',
            'required for unattended desktop availability after login',
            bool(launch_at_login),
        ),
        SetupRequirement(
            'auto_start_server',
            'Automatically start the desktop server',
            'required for unattended Desktop readiness',
            bool(auto_start_server),
        ),
    ])
    return requirements



def evaluate_prelogin_requirements(
    admin_state: Mapping[str, object] | None,
) -> list[SetupRequirement]:
    """Return requirements for the optional reboot-to-LoginWindow remote-login path.

    These requirements deliberately do not participate in the host's normal
    READY state. A logged-in Aqua host can be fully ready for Desktop, Files,
    System, Terminal and Projects even when pre-login capability verification
    has not been run for the current build.
    """
    if admin_state is None:
        return [
            SetupRequirement(
                'prelogin_release_identity',
                'Apple-managed pre-login capabilities',
                'requires Developer ID provisioning with Persistent Content Capture and CoreHID Virtual Device entitlements',
                False,
            ),
            SetupRequirement(
                'prelogin_agent',
                'LoginWindow pre-login agent',
                'required for remote Desktop before macOS login after reboot',
                False,
            ),
            SetupRequirement(
                'prelogin_capability_proof',
                'Verified LoginWindow capture and CoreHID input',
                'requires a current physical LoginWindow proof for this exact Mac/build',
                False,
            ),
        ]

    installed = bool(admin_state.get('installed'))
    return [
        SetupRequirement(
            'prelogin_release_identity',
            'Apple-managed pre-login capabilities',
            'requires Developer ID provisioning with Persistent Content Capture and CoreHID Virtual Device entitlements',
            bool(admin_state.get('prelogin_release_identity_ready')),
        ),
        SetupRequirement(
            'prelogin_agent',
            'LoginWindow pre-login agent',
            'required for remote Desktop before macOS login after reboot',
            installed
            and bool(admin_state.get('prelogin_agent_exists'))
            and bool(admin_state.get('prelogin_agent_contract_ready')),
        ),
        SetupRequirement(
            'prelogin_capability_proof',
            'Verified LoginWindow capture and CoreHID input',
            'requires a current physical LoginWindow proof for this exact Mac/build',
            installed and bool(admin_state.get('prelogin_probe_ready')),
        ),
    ]

def prelogin_readiness_state(admin_state: Mapping[str, object] | None) -> str:
    """Return VERIFIED, UNVERIFIED, NOT_READY or UNKNOWN for reboot pre-login."""
    if admin_state is None:
        return 'UNKNOWN'
    requirements = {item.key: item for item in evaluate_prelogin_requirements(admin_state)}
    release_identity = requirements['prelogin_release_identity'].ready
    agent = requirements['prelogin_agent'].ready
    proof = requirements['prelogin_capability_proof'].ready
    if not release_identity or not agent:
        return 'NOT_READY'
    if proof:
        return 'VERIFIED'
    return 'UNVERIFIED'


def missing_setup_requirements(requirements: list[SetupRequirement]) -> list[SetupRequirement]:
    return [item for item in requirements if not item.ready]
