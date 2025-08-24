from pathlib import PosixPath
from typing import List

from fabric import Connection, Config, task


CONFIG_BASE_URL = "https://api.github.com/repos/cluebotng/component-configs/contents"
TOOL_BASE_DIR = PosixPath("/data/project")


def _get_target_tools() -> List[str]:
    return [
        config.name.split(".yaml")[0]
        for config in PosixPath(__file__).parent.glob("*.yaml")
    ]


def _setup_component_configs(tool_name: str):
    config_url = f'{CONFIG_BASE_URL.rstrip("/")}/{tool_name}.yaml'
    print(f'[{tool_name}] applying {config_url}')

    c = Connection(
        "login.toolforge.org",
        config=Config(
            overrides={
                "sudo": {"user": f"tools.{tool_name}", "prefix": "/usr/bin/sudo -ni"}
            }
        ),
    )
    c.sudo(
        f"bash -c 'curl -s --fail {config_url} | "
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components config create'",
    )


def _generate_workflow(tool_name: str):
    # We do this to avoid `yaml` as a dep, it's simple enough
    config = f'name: \'Trigger deploy for {tool_name}\'\n'
    config += f'on: {{ push: {{ branches: [ main ], paths: [ \'{tool_name}.yaml\' ] }} }}\n'
    config += 'jobs:\n'
    config += '  deploy:\n'
    config += '    runs-on: ubuntu-latest\n'
    config += f'    environment: \'{tool_name}\'\n'
    config += '    steps:\n'
    config += '      - uses: cluebotng/ci-toolforge-deploy@main\n'
    config += '        with:\n'
    config += f'          tool: \'{tool_name}\'\n'
    config += '          token: \'${{ secrets.TOOLFORGE_DEPLOY_TOKEN }}\'\n'
    return config


@task()
def create_workflows(_ctx):
    """Generate Github workflows for each tool"""
    for tool_name in _get_target_tools():
        workflow_file = PosixPath(__file__).parent / ".github" / "workflows" / f"{tool_name}.yaml"
        with workflow_file.open('w') as fh:
            fh.write(_generate_workflow(tool_name))


@task()
def setup(_ctx):
    """Ensure the tool accounts have component configs setup."""
    for tool_name in _get_target_tools():
        _setup_component_configs(tool_name)
