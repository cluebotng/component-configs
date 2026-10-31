import dataclasses
import io
import json
import os
import sys
import time
from pathlib import PosixPath
from typing import Any

import requests
import yaml
from fabric import Config, Connection, task
from requests.exceptions import HTTPError

TARGET_USER = os.environ.get("TARGET_USER")
TOOL_BASE_DIR = PosixPath("/data/project")
EMIT_LOG_MESSAGES = os.environ.get("EMIT_LOG_MESSAGES", "true") == "true"


@dataclasses.dataclass
class NetworkPolicyPodIngress:
    name: str

    def as_k8s_object(self) -> dict[str, Any]:
        return {"podSelector": {"matchLabels": {"app.kubernetes.io/name": self.name}}}

    @staticmethod
    def from_values(data: dict[str, Any]) -> "NetworkPolicyPodIngress":
        return NetworkPolicyPodIngress(name=data["pod"])


@dataclasses.dataclass
class NetworkPolicyNamespaceIngress:
    name: str

    def as_k8s_object(self) -> dict[str, Any]:
        return {"namespaceSelector": {"matchLabels": {"name": self.name}}}

    @staticmethod
    def from_values(data: dict[str, Any]) -> "NetworkPolicyNamespaceIngress":
        return NetworkPolicyNamespaceIngress(name=data["namespace"])


@dataclasses.dataclass
class NetworkPolicy:
    name: str
    match: str
    allow: list[NetworkPolicyPodIngress | NetworkPolicyNamespaceIngress]
    delete: bool

    @property
    def k8s_type(self) -> "str":
        return "NetworkPolicy"

    def __str__(self) -> str:
        return f"NetworkPolicy(name={self.name})"

    def as_k8s_object(self) -> dict[str, Any]:
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": self.name,
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": self.match,
                    },
                },
                "ingress": [{"from": [entry.as_k8s_object() for entry in self.allow]}],
            },
        }

    @staticmethod
    def from_values(data: dict[str, Any]) -> "NetworkPolicy":
        should_delete = data.get("delete", False)

        allow_from = []
        if not should_delete:
            for entry in data.get("allow", []):
                if "namespace" in entry:
                    allow_from.append(NetworkPolicyNamespaceIngress.from_values(entry))
                elif "pod" in entry:
                    allow_from.append(NetworkPolicyPodIngress.from_values(entry))

        return NetworkPolicy(
            name=data["name"],
            match=data["match"],
            allow=allow_from,
            delete=should_delete,
        )


@dataclasses.dataclass
class StaticFile:
    source: str
    target: str
    mode: str
    replace: dict[str, str]

    def load(self) -> str:
        path = PosixPath(__file__).parent / "config" / "static-files" / "files" / self.source
        with path.open("r") as fh:
            return fh.read()

    @staticmethod
    def from_values(data: dict[str, Any]) -> "StaticFile":
        return StaticFile(
            source=data["source"],
            target=data["target"],
            mode=data.get("mode", "0600"),
            replace=data.get("replace", {}),
        )


def _raise_for_status_with_no_url(response: requests.Response) -> None:
    if 400 <= response.status_code < 500:
        raise HTTPError(f"{response.status_code} Client Error: {response.reason}", response=response)

    elif 500 <= response.status_code < 600:
        raise HTTPError(f"{response.status_code} Server Error: {response.reason}", response=response)


def _get_connection_for_tool(tool_name: str) -> Connection:
    return Connection(
        "login.toolforge.org",
        config=Config(overrides={"sudo": {"user": f"tools.{tool_name}", "prefix": "/usr/bin/sudo -ni"}}),
    )


def _get_target_tools() -> list[str]:
    return [config.name.split(".yaml")[0] for config in PosixPath(__file__).parent.glob("*.yaml")]


def _get_network_policies() -> dict[str, list[NetworkPolicy]]:
    config = {}
    config_path = PosixPath(__file__).parent / "config" / "network-policies"
    if config_path.is_dir():
        for path in config_path.glob("*.yaml"):
            with path.open("r") as fh:
                config.update({path.name.split(".yaml")[0]: yaml.load(fh.read(), Loader=yaml.SafeLoader)})

    return {
        tool_name: [NetworkPolicy.from_values(config) for config in entries] for tool_name, entries in config.items()
    }


def _get_static_files(target_tools: list[str]) -> dict[str, list[StaticFile]]:
    config = {}
    config_path = PosixPath(__file__).parent / "config" / "static-files"
    if config_path.is_dir():
        for path in config_path.glob("*.yaml"):
            with path.open("r") as fh:
                config.update({path.name.split(".yaml")[0]: yaml.load(fh.read(), Loader=yaml.SafeLoader)})

    global_entries = [StaticFile.from_values(config) for config in config.get("_global", [])]

    return {
        tool_name: global_entries + [StaticFile.from_values(config) for config in config.get(tool_name, [])]
        for tool_name in target_tools
    }


def _setup_component_configs(c: Connection, tool_name: str):
    with (PosixPath(__name__).parent / f"{tool_name}.yaml").open("r") as fh:
        config = fh.read()

    c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components config create",
        in_stream=io.StringIO(config),
    )


def _get_deployment_token(c: Connection, tool_name: str):
    deployment_token = c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components deploy-token show --json | jq -r .token",
        hide="stdout",
    ).stdout.strip()

    if deployment_token == "":
        print(f"Creating deploy-token for {tool_name}")
        c.sudo(
            f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components deploy-token create",
            hide="stdout",
        )
        return _get_deployment_token(c, tool_name)

    return deployment_token


def _apply_kubernetes_object(c: Connection, k8s_obj: dict[str, Any]) -> bool:
    obj_as_yaml = yaml.dump(k8s_obj)
    ret = c.sudo("kubectl apply -f-", hide="both", in_stream=io.StringIO(obj_as_yaml))
    if ret.exited != 0:
        print(f"kubectl apply failed: {ret.stdout} / {ret.stderr}")
    return ret.exited == 0


def _delete_kubernetes_object(c: Connection, obj_type: str, obj_name: str) -> bool:
    ret = c.sudo(f"kubectl delete {obj_type} {obj_name} || true", hide="both")

    if len(ret.stderr) != 0 and "Error from server (NotFound)" not in ret.stderr:
        print(f"kubectl delete failed: {ret.stdout} / {ret.stderr}")
        return False

    return True


def _ensure_kubernetes_object(
    c: Connection,
    tool_name: str,
    obj: NetworkPolicy,
) -> bool:
    if hasattr(obj, "delete") and obj.delete:
        return _delete_kubernetes_object(c, obj.k8s_type, obj.name)

    # Whack the object into the cluster
    print(f"Applying {obj}")
    return _apply_kubernetes_object(c, obj.as_k8s_object())


def _ensure_static_file(c: Connection, tool_name: str, static_file: StaticFile) -> bool:
    contents = static_file.load()
    for k, v in static_file.replace.items():
        contents = contents.replace(f"{{{{ {k} }}}}", v)
    target_path = TOOL_BASE_DIR / tool_name / static_file.target

    print(f"Writing {static_file.source} to {target_path.absolute()} ({static_file.mode})")
    ret = c.sudo(
        f"bash -c \"install -D -m {static_file.mode} /dev/null '{target_path}' && cat > '{target_path}'\"",
        hide="both",
        in_stream=io.StringIO(contents),
    )
    if ret.exited != 0:
        print(f"file write failed: {ret.stdout} / {ret.stderr}")
    return ret.exited == 0


def _dologmsg(tool_name: str, message: str):
    c = _get_connection_for_tool(tool_name)

    if EMIT_LOG_MESSAGES:
        feed_message = f"#wikimedia-cloud-feed !log tools.{tool_name} {message}"
        c.run(f"echo '{feed_message}' > /dev/tcp/wm-bot.wm-bot.wmcloud.org/64835 || true")


def _start_deployment(tool_name: str, deploy_token: str, force_run: bool, force_build: bool) -> str | None:
    r = requests.post(
        f"https://api.svc.toolforge.org/components/v1/tool/{tool_name}/deployment",
        params={
            "token": deploy_token,
            "force-run": force_run,
            "force-build": force_build,
        },
    )
    if r.status_code == 409:
        print(f"Deployment already in progress for {tool_name} - multiple/queueing not supported currently!")
        return None

    _raise_for_status_with_no_url(r)
    return r.json()["data"]["deploy_id"]


def _get_deployment_status(tool_name: str, deploy_id: str, deploy_token: str) -> str:
    r = requests.get(
        f"https://api.svc.toolforge.org/components/v1/tool/{tool_name}/deployment/{deploy_id}",
        params={"token": deploy_token},
    )
    _raise_for_status_with_no_url(r)
    return r.json()["data"].get("status", "pending")


def _execute_deployment(
    tool_name: str, deploy_token: str, force_run: bool, force_build: bool
) -> tuple[str | None, bool]:
    deploy_id = _start_deployment(tool_name, deploy_token, force_run, force_build)
    if deploy_id is None:
        return None, False

    print(f"Started deployment: {deploy_id}")
    while True:
        deployment_status = _get_deployment_status(tool_name, deploy_id, deploy_token)
        if deployment_status == "successful":
            print("Deployment has finished successfully")
            return deploy_id, True

        if deployment_status in ["pending", "running"]:
            time.sleep(1)
            continue

        print(f"Deployment is not pending, running or successful: {deployment_status}")
        return deploy_id, False


def _show_deployment(c: Connection, tool_name: str, deploy_id: str) -> None:
    c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components deployment show '{deploy_id}'",
    )


def _generate_workflow(tool_name: str):
    # We do this to avoid `yaml` as a dep, it's simple enough
    config = f"name: '{tool_name}'\n"
    config += "on: { push: { branches: [ main ], paths: [ "
    config += f"'{tool_name}.yaml', "
    config += "'config/general.yaml', "
    config += f"'config/network-policies/{tool_name}.yaml', "
    config += "] }, workflow_dispatch: { } }\n"
    config += f"concurrency:\n  group: {tool_name}\n"
    config += "jobs:\n"
    # Until T401868 is resolved, update the tool with the config we want deployed
    # Note: the config will not be re-fetched as `source_url` cannot be rewritten on the same sha... so this is '1 off'
    config += "  update-network-policies:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v6\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: update-network-policies\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"

    config += "  update-component-config:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    needs: [update-network-policies]\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v6\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: update-component-config\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"

    config += "  execute-deployment:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    needs: [update-network-policies, update-component-config]\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v6\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: execute-deployment\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"

    config += "  dologmsg-success:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    needs: [update-network-policies, update-component-config, execute-deployment]\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v6\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: dologmsg\n"
    config += "          argument: \"--message='Deployment completed: "
    config += "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }} "
    config += "(https://github.com/cluebotng/component-configs/commits/${{ github.sha || github.ref }})'\"\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"

    config += "  dologmsg-failure:\n"
    config += "    runs-on: ubuntu-latest\n"
    config += "    needs: [update-network-policies, update-component-config, execute-deployment]\n"
    config += "    if: ${{ failure() }}\n"
    config += "    steps:\n"
    config += "      - uses: actions/checkout@v6\n"
    config += "      - uses: cluebotng/ci-execute-fabric@main\n"
    config += "        with:\n"
    config += f"          user: '{tool_name}'\n"
    config += "          task: dologmsg\n"
    config += "          argument: \"--message='Deployment failed: "
    config += "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }} "
    config += "(https://github.com/cluebotng/component-configs/commits/${{ github.sha || github.ref }})'\"\n"
    config += "          ssh_key: ${{ secrets.CI_SSH_KEY }}\n"
    return config


@task()
def rotate_deployment_token(_ctx):
    """Rotate the deployment token"""
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)
            c.sudo(
                f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components deploy-token refresh --yes-im-sure",
                hide="both",
            )
            print(f"Rotated deployment token for {tool_name}")


@task()
def create_workflows(_ctx):
    """Generate Github workflows for each tool"""
    for tool_name in _get_target_tools():
        workflow_file = PosixPath(__file__).parent / ".github" / "workflows" / f"{tool_name}.yaml"
        with workflow_file.open("w") as fh:
            fh.write(_generate_workflow(tool_name))


@task()
def update_component_config(_ctx):
    """Ensure the tool accounts have a component config setup."""
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)
            print("Applying deployment config")
            _setup_component_configs(c, tool_name)


@task()
def execute_deployment(_ctx, force_run: bool = False, force_build: bool = False):
    """Execute a component deployment for a tool account."""
    # This can also be done externally using the deploy token, unfortunately when using environments in Github
    # actions, the shared secrets are not passed through, so to avoid having to copy the key into every environment,
    # fetch the deploy token from the tool account....
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)
            if deploy_token := _get_deployment_token(c, tool_name):
                deploy_id, deploy_success = _execute_deployment(tool_name, deploy_token, force_run, force_build)
                if not deploy_success:
                    print(f"Deployment failed for {tool_name} ({deploy_id})")
                    if TARGET_USER:
                        # If we are executing for a single tool, the exit with a failure code
                        if deploy_id is not None:
                            _show_deployment(c, tool_name, deploy_id)
                        sys.exit(1)


@task()
def update_network_policies(_ctx):
    """Execute a network policy deployment for a tool account."""
    network_policies = _get_network_policies()
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)

            if tool_network_policies := network_policies.get(tool_name):
                is_success = True
                for network_policy in tool_network_policies:
                    is_success &= _ensure_kubernetes_object(c, tool_name, network_policy)
                if not is_success:
                    print(f"Deployment failed for {tool_name}")
                    if TARGET_USER:
                        # If we are executing for a single tool, the exit with a failure code
                        sys.exit(1)


@task()
def update_static_files(_ctx):
    """Execute a static file deployment for a tool account."""
    target_tools = _get_target_tools()
    static_files = _get_static_files(target_tools)
    for tool_name in target_tools:
        if TARGET_USER is None or tool_name == TARGET_USER:
            c = _get_connection_for_tool(tool_name)

            if tool_static_files := static_files.get(tool_name):
                is_success = True
                for static_file in tool_static_files:
                    is_success &= _ensure_static_file(c, tool_name, static_file)
                if not is_success:
                    print(f"Deployment failed for {tool_name}")
                    if TARGET_USER:
                        # If we are executing for a single tool, the exit with a failure code
                        sys.exit(1)


@task()
def purge_tool_account(_ctx):
    """Remove all configuration from a tool account."""
    if not TARGET_USER:
        print("Will only purge tool accounts for a single tool")
        sys.exit(1)

    c = _get_connection_for_tool(TARGET_USER)

    # Remove jobs
    print(f"Flushing jobs for {TARGET_USER}")
    c.sudo(f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / TARGET_USER}' toolforge jobs flush")

    # Remove builds from builds-api
    print(f"Removing builds for {TARGET_USER}")
    raw_build_output = c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / TARGET_USER}' toolforge build list --json",
        hide="stdout",
    ).stdout.strip()

    for build in json.loads(raw_build_output).get("builds", []):
        c.sudo(
            f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / TARGET_USER}' toolforge build delete -y {build['build_id']}",
            hide="stdout",
        )

    # Remove images from harbor
    c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / TARGET_USER}' toolforge build clean -y",
        hide="stdout",
    )

    # Remove deployments
    print(f"Removing deployments for {TARGET_USER}")
    raw_deployment_list_output = c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / TARGET_USER}' toolforge components deployment list --json",
        hide="both",
        warn=True,
    )
    raw_deployment_output = raw_deployment_list_output.stdout or "{}"
    if (
        raw_deployment_list_output.exited != 0
        and f"No deployments found for tool: {TARGET_USER}" not in raw_deployment_list_output.stderr
    ):
        raise RuntimeError(
            f"deployment list failed with {raw_deployment_list_output.exited}\n"
            f"Stdout:\n{raw_deployment_list_output.stdout}\n"
            f"Stderr: \n{raw_deployment_list_output.stderr}\n"
        )

    for deployment in json.loads(raw_deployment_output).get("data", {}).get("deployments", []):
        c.sudo(
            f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / TARGET_USER}' "
            f"toolforge components deployment delete --yes-im-sure {deployment['deploy_id']}",
            hide="both",
        )

    # Clean k8 resources
    print(f"Removing k8 objects for {TARGET_USER}")
    c.sudo("kubectl delete --all NetworkPolicy", hide="both")
    c.sudo("kubectl delete --all HttpRoute", hide="both")


@task()
def deploy(ctx):
    update_network_policies(ctx)
    update_component_config(ctx)
    execute_deployment(ctx)
    update_static_files(ctx)


@task()
def clean_deploy(ctx):
    purge_tool_account(ctx)
    deploy(ctx)


@task()
def dologmsg(ctx, message):
    if TARGET_USER is None:
        print("TARGET_USER must be specified for dologmsg")
        sys.exit(1)

    _dologmsg(TARGET_USER, message)


@task()
def print_tools(ctx):
    for tool_name in _get_target_tools():
        print(tool_name)
