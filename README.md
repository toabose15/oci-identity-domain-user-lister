# OCI Identity Domain User Lister

This utility runs from an Oracle Cloud Infrastructure Compute instance and lists users from a selected IAM identity domain. It uses instance principal authentication, so it does not require an OCI config file, API key, or user credentials on the instance.

The main script is `oci-domain-users.py`.

Suggested GitHub repository name: `oci-identity-domain-user-lister`.

## What It Does

- Detects the Compute instance region from the OCI instance metadata service.
- Authenticates to OCI using the instance principal signer.
- Discovers accessible compartments and identity domains.
- Lets an operator select an identity domain interactively.
- Optionally accepts a domain OCID for non-interactive runs.
- Fetches domain home and replica endpoint details.
- Measures endpoint latency and selects the lowest-latency reachable endpoint.
- Lists identity domain users with pagination.
- Prints users as a simple table or JSON.

The script is read-only. It lists compartments, domains, domain details, and users. It does not create, update, or delete IAM resources.

## Files

```text
oci-domain-users.py    Main script
requirements.txt       Python dependencies
```

## Prerequisites

### OCI Runtime

- An OCI Compute instance.
- Network access from the instance to OCI public service endpoints.
- Access to the instance metadata service at `http://169.254.169.254`.
- A dynamic group that includes the Compute instance.
- IAM policies granting that dynamic group access to inspect compartments, read domains, and inspect users.

### Python Runtime

- Python 3.8 or newer.
- The packages listed in `requirements.txt`:

```bash
pip3 install -r requirements.txt
```

## Dynamic Group

Create a dynamic group that includes the Compute instance running the script.

For one specific instance:

```text
instance.id = '<compute_instance_ocid>'
```

For all instances in a compartment:

```text
instance.compartment.id = '<compute_compartment_ocid>'
```

For instances with a specific tag:

```text
tag.<tag_namespace>.<tag_key>.value='<tag_value>'
```

Dynamic group membership and policy changes can take time to propagate.

## Required IAM Policies

Replace `<dynamic-group-name>` with your dynamic group name.

If the dynamic group is in the Default identity domain:

```text
Allow dynamic-group <dynamic-group-name> to inspect compartments in tenancy
Allow dynamic-group <dynamic-group-name> to read domains in tenancy
Allow dynamic-group <dynamic-group-name> to inspect users in tenancy
```

If the dynamic group is in a non-default identity domain:

```text
Allow dynamic-group <domain-name>/<dynamic-group-name> to inspect compartments in tenancy
Allow dynamic-group <domain-name>/<dynamic-group-name> to read domains in tenancy
Allow dynamic-group <domain-name>/<dynamic-group-name> to inspect users in tenancy
```

For names with spaces or special characters, quote the subject:

```text
Allow dynamic-group '<domain-name>'/'<dynamic-group-name>' to inspect users in tenancy
```

### Least Privilege Option

Interactive mode needs compartment and domain discovery permissions:

```text
Allow dynamic-group <dynamic-group-name> to inspect compartments in tenancy
Allow dynamic-group <dynamic-group-name> to read domains in tenancy
Allow dynamic-group <dynamic-group-name> to inspect users in tenancy
```

Non-interactive mode with `--domain-ocid` does not need to discover compartments or list domains, but it still needs to read the selected domain and inspect users:

```text
Allow dynamic-group <dynamic-group-name> to read domains in tenancy
Allow dynamic-group <dynamic-group-name> to inspect users in tenancy where target.resource.domain.id = '<domain_ocid>'
```

## Usage

Run from the Compute instance:

```bash
python3 oci-domain-users.py
```

The script will:

1. Detect the instance region.
2. Authenticate with instance principal credentials.
3. List accessible identity domains.
4. Prompt you to select a domain.
5. Pick the best reachable domain endpoint.
6. List users from the selected domain.

## Non-Interactive Usage

Use `--domain-ocid` to skip the domain selection prompt:

```bash
python3 oci-domain-users.py --domain-ocid ocid1.domain.oc1...
```

Limit the number of users:

```bash
python3 oci-domain-users.py --domain-ocid ocid1.domain.oc1... --user-limit 50
```

Print JSON:

```bash
python3 oci-domain-users.py --domain-ocid ocid1.domain.oc1... --format json
```

Save JSON output while keeping operational logs on the terminal:

```bash
python3 oci-domain-users.py --domain-ocid ocid1.domain.oc1... --format json > users.json
```

Operational messages are printed to `stderr`, so redirected JSON output stays clean.

## Command Options

```text
--domain-ocid   Identity domain OCID to use without prompting.
--format        Output format: table or json. Default: table.
--user-limit    Maximum number of users to print.
```

## Overall Code Workflow

```text
parse_args()
  Reads optional CLI flags.

get_instance_region()
  Calls the OCI metadata service and gets canonicalRegionName.

InstancePrincipalsSecurityTokenSigner()
  Creates the signer used for OCI API calls.

get_identity_client()
  Creates the IAM IdentityClient in the instance region.

get_selected_domain()
  Uses --domain-ocid if supplied, otherwise discovers domains and prompts.

discover_domains()
  Lists accessible compartments and identity domains using OCI pagination.

choose_domain_endpoint()
  Builds a list of home and replica endpoints, measures latency, and selects the best reachable endpoint.

get_identity_domain_client()
  Creates the IdentityDomainsClient for the selected domain endpoint.

list_users()
  Lists users with SCIM-style pagination.

print_users()
  Prints table or JSON output.
```

## Common Uses

- Quickly inspect users in an OCI identity domain from a trusted Compute instance.
- Validate instance principal access to IAM and Identity Domains APIs.
- Compare domain home and replica endpoint reachability from a Compute instance.
- Export identity domain users as JSON for reporting or downstream scripts.
- Run a simple operator-driven IAM discovery workflow over SSH.
- Run a non-interactive user inventory task with a known domain OCID.

## Troubleshooting

### Failed to determine region

The script could not reach the instance metadata service. Confirm that it is running on an OCI Compute instance and that metadata access is available.

### NotAuthorizedOrNotFound

The instance principal is not authorized, the dynamic group does not include the instance, or policy propagation has not completed. Check the dynamic group rule and IAM policy statements.

### No identity domains found

The dynamic group likely lacks `read domains` access, or the script is only able to see compartments that do not contain identity domains.

### Use --domain-ocid when running non-interactively

The script was run without an attached terminal and could not show the selection prompt. Supply `--domain-ocid`.

### Empty or partial user list

Confirm the dynamic group has `inspect users` access for the target identity domain. If a least-privilege policy uses `target.resource.domain.id`, confirm the domain OCID is correct.

## References

- OCI dynamic group policies: https://docs.oracle.com/iaas/Content/Identity/callresources/Writing_Policies_for_Dynamic_Groups.htm
- OCI policy subjects: https://docs.oracle.com/en-us/iaas/Content/Identity/policysyntax/subject.htm
- IAM with Identity Domains policy reference: https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/iampolicyreference.htm
- OCI Python SDK: https://docs.oracle.com/en-us/iaas/tools/python/latest/
