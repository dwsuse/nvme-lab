"""'lab ci': run a command in one of nvme-cli's CI images on the CI host,
on a tree that 'lab sync' put there."""

from . import remote

REGISTRY = 'ghcr.io/linux-nvme'


def image_ref(image):
    """debian -> ghcr.io/linux-nvme/debian:latest; a full ref stays."""
    if '/' in image:
        return image
    if ':' not in image:
        image += ':latest'
    return f'{REGISTRY}/{image}'


def docker_argv(image, src, command, pull=False):
    # The tree belongs to root on the host, the container's git refuses
    # it without safe.directory.
    inner = 'git config --global --add safe.directory /src && ' + command
    argv = ['docker', 'run', '--rm']
    if pull:
        argv += ['--pull', 'always']
    argv += ['-v', f'{src}:/src', '-w', '/src', image_ref(image),
             'sh', '-c', inner]
    return argv


def ci_argv(cfg, host, image, src, command, pull=False):
    return remote.ssh_argv(cfg, host, docker_argv(image, src, command, pull))
