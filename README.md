# Toolforge Component Configs

This repo holds the Toolforge component configs for ClueBot related tool accounts.

Reference: [https://wikitech.wikimedia.org/wiki/Help:Toolforge/Deploy_your_tool](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Deploy_your_tool).

## Deployments

We use the logic contained within `fabfile.py`, the component config is updated via ssh, then the deployment is triggered via HTTP.

Additional objects, such as the `Ingress` and `NetworkPolicy` objects are handled via SSH.

Any changes to a tool's configuration is picked up via GitHub actions and deployed using secrets contained at the GitHub org level.

Internal repos update the configuration on releases via a GitHub application, the key of which is contained at the GitHub org level (access granted per-repo).

Manual changes are essentially limited to new components, runtime resource changes, and object (`Ingress`/`NetworkPolicy`) changes.

## Adding a new tool (user)
1. Ensure `DamianZaremba Scripts` has access via toolsadmin
2. Create `<tool>.yaml` in the root with the relevant components
3. Run `fab create-workflows` to create the GitHub actions config
4. Commit the files
5. Have a cup of coffee

## Adding a new dependency (repo)
1. Create the repo, grant it public access
2. Edit the secret under the GitHub org to grant the repo access `CI_COMPONENT_CONFIGS_APP_KEY` (`CI_SSH_KEY` is only needed for this repo and legacy repos)
3. Create a deployment workflow which calls `cluebotng/ci-update-component-ref`
4. ???
5. Profit

Note: `CI_SSH_KEY` has literal SSH access to the tool accounts, thus has access to all the secrets, it should be highly restricted.
