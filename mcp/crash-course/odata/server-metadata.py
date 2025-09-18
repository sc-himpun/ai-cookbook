# -*- coding: utf-8 -*-
import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional, Any, List, Union
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from urllib.parse import quote
import xml.etree.ElementTree as ET
from collections import defaultdict
import statistics
import io
from typing import Dict, List, Union
import json
from typing import List, Dict
from collections import defaultdict
from collections import defaultdict
import re

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
PORT = int(os.getenv("ODATA_MCP_PORT", "8016"))
# SAP_USERNAME = os.getenv("SAP_USERNAME")
# SAP_PASSWORD = os.getenv("SAP_PASSWORD")

# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("odata-mcp")
MAX_CHARS = 8000

# DEFAULT_BASE_URL = "https://services.odata.org/V4/Northwind/Northwind.svc"
DEFAULT_BASE_URL = "https://sapes5.sapdevcenter.com/sap/opu/odata/IWBEP/GWSAMPLE_BASIC"


""" Metadata structure example:
metadata = {
    "base_url": "https://your-odata-server.com/odata",  # required
    "username": "your-username",                        # optional
    "password": "your-password"                         # optional
}
"""

def get_odata_creds(metadata: Optional[dict]) -> tuple[str, Optional[tuple]]:
    """
    Extract base_url and basic auth from metadata.
    Supports SAP authentication and generic OData.
    """
    if not metadata or "base_url" not in metadata:
        raise Exception("❌ Missing base_url in metadata for OData")

    base_url = metadata["base_url"].rstrip("/")
    username = metadata.get("username")
    password = metadata.get("password")

    auth = (username, password) if username and password else None
    return base_url, auth




def get_entity_set_for_type(entity_set: str, schema_metadata: Optional[dict]) -> str:
    """
    Given an entity type and parsed metadata (schema info), returns the corresponding entity set name.
    """
    if not schema_metadata or "entity_sets" not in schema_metadata:
        return entity_set  # fallback
    
    for es_name, etype in schema_metadata.get("entity_sets", {}).items():
        if etype == entity_set:
            return es_name
    return entity_set






def truncate_response(data: Any) -> str:
    """
    Converts data to string and truncates it if it exceeds MAX_CHARS.
    """
    stringified = str(data)
    if len(stringified) > MAX_CHARS:
        return stringified[:MAX_CHARS] + f"\n...(truncated to {MAX_CHARS} chars)"
    return stringified




def make_odata_request(url, headers=None, params=None, metadata: Optional[dict] = None):
    headers = headers or {}
    if "Accept" not in headers:
        if url.endswith("/$metadata"):
            headers["Accept"] = "application/xml"
        elif url.endswith("/$count"):
            pass
        else:
            headers["Accept"] = "application/json"

    base_url, auth = get_odata_creds(metadata)
    return requests.get(url, headers=headers, params=params or {}, auth=auth)


@mcp.tool(name="odata_search")
def search_entity_set(
    entity_set: str,
    metadata: dict,
    schema_metadata: Optional[dict] = None,
    top: int = 5
) -> str:
    """
    Search and return up to 'top' records from the specified OData entity set.

    Parameters:
    - entity_set: Logical name or entity type (e.g., "SalesOrder").
    - metadata: Dictionary from UI/client with connection details. Expected keys:
        - base_url (required): OData service root URL
        - username (optional): Basic auth username
        - password (optional): Basic auth password
    - schema_metadata: Parsed OData $metadata content (used to resolve entity type to entity set name).
    - top: Number of records to fetch (default 5).

    Behavior:
    - Uses schema_metadata to resolve the actual entity set name if available.
    - Uses metadata to resolve the base URL and authentication.
    """
    # Resolve correct entity set if mapping is available
    resolved_entity_set = get_entity_set_for_type(entity_set, schema_metadata)

    # Extract connection credentials
    base_url, _ = get_odata_creds(metadata)

    # Construct query URL
    url = f"{base_url.rstrip('/')}/{resolved_entity_set}?$top={top}"
    headers = {"Accept": "application/json"}

    # Make the actual request
    resp = make_odata_request(url, headers=headers, metadata=metadata)

    if not resp.ok:
        return f"❌ Failed to fetch data: {resp.status_code} - {resp.text}"
    
    data = resp.json().get("d", {}).get("results", [])
    if not data:
        return f"ℹ️ No records found in {resolved_entity_set}."

    return truncate_response(json.dumps(data, indent=2))


@mcp.tool(name="odata_describe_entity_set")
def describe_entity_set(entity_set: str, metadata: dict) -> str:
    """
    Describes the schema (fields and types) of the specified OData entity set.

    Parameters:
    - entity_set: Name of the OData entity set to describe.
    - metadata: UI/client-passed metadata with keys:
        - base_url (required): Base URL of the OData service
        - username (optional): Basic auth username
        - password (optional): Basic auth password
    """
    base_url, _ = get_odata_creds(metadata)
    url = f"{base_url.rstrip('/')}/$metadata"

    resp = make_odata_request(url, metadata=metadata)
    if not resp.ok:
        return f"❌ Failed to fetch metadata: {resp.status_code}"

    ns = {
        'edmx': 'http://docs.oasis-open.org/odata/ns/edmx',
        'edm': 'http://docs.oasis-open.org/odata/ns/edm'
    }

    root = ET.fromstring(resp.text)
    
    # Get entity type corresponding to the provided entity set
    entity_type = next(
        (
            es.attrib.get("EntityType").split('.')[-1]
            for es in root.findall(".//edm:EntitySet", ns)
            if es.attrib.get("Name") == entity_set
        ),
        None
    )

    if not entity_type:
        return f"Entity set '{entity_set}' not found."

    fields = [
        f"{p.attrib.get('Name')}: {p.attrib.get('Type')}"
        for et in root.findall(f".//edm:EntityType[@Name='{entity_type}']", ns)
        for p in et.findall("edm:Property", ns)
    ]

    return truncate_response(f"Schema for '{entity_set}':\n" + "\n".join(fields)) if fields else "No properties found."



def quote_key_if_needed(key: str) -> str:
    """
    Ensures the key is properly quoted for OData requests if needed.
    """
    if key.isdigit() or key.startswith("("):  # numeric or composite key
        return key
    if not (key.startswith("'") and key.endswith("'")):
        return f"'{key}'"
    return key


@mcp.tool(name="odata_entity_by_key")
def get_entity_by_key(entity_set: str, key: str, metadata: dict) -> str:
    """
    Retrieves a single entity from the specified entity set by its key.

    Parameters:
    - entity_set: Name of the OData entity set
    - key: Entity key to fetch (quoted if needed)
    - metadata: Contains 'base_url' and optionally 'username', 'password'
    """
    base_url, _ = get_odata_creds(metadata)
    key = quote_key_if_needed(key)

    select_fields = get_select_fields_excluding_binary(entity_set, metadata)
    select_query = f"?$select={select_fields}&$format=json" if select_fields else "?$format=json"

    url = f"{base_url.rstrip('/')}/{entity_set}({key}){select_query}"
    resp = make_odata_request(url, metadata=metadata)

    if not resp.ok:
        return f"❌ Failed to fetch entity: {resp.status_code} - {resp.text}"

    try:
        return truncate_response(resp.json())
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"


@mcp.tool(name="odata_filter_by_property")
def odata_filter_by_property(
    metadata: dict,
    entity_set: str,
    property_name: str,
    value: Union[str, int, float, bool],
    top: int = 50,
) -> List[Dict]:
    """
    Filter entities in an OData entity set where a property equals a specific value.
    
    Parameters:
    - metadata: Contains 'base_url' and optional 'username', 'password'
    - entity_set: Name of the OData entity set
    - property_name: The field to filter on
    - value: The value to match
    - top: Maximum number of results to return
    """
    from urllib.parse import quote

    base_url, _ = get_odata_creds(metadata)
    filter_str = (
        f"{property_name} eq {quote(str(value))}"
        if isinstance(value, (int, float, bool))
        else f"{property_name} eq '{quote(str(value))}'"
    )

    url = f"{base_url.rstrip('/')}/{entity_set}?$filter={filter_str}&$top={top}"
    headers = {"Accept": "application/json"}
    resp = make_odata_request(url, headers=headers, metadata=metadata)

    if not resp.ok:
        raise Exception(f"❌ Filter failed: {resp.status_code} - {resp.text}")

    return resp.json().get("d", {}).get("results", [])



@mcp.tool(name="odata_sort_by_property")
def odata_sort_by_property(
    metadata: dict,
    entity_set: str,
    property_name: str,
    order: str = "asc",  # or "desc"
    top: int = 50,
) -> List[Dict]:
    """
    Sorts entities in an OData entity set by a given property.

    Parameters:
    - metadata: Contains 'base_url' and optional 'username', 'password'
    - entity_set: Name of the OData entity set
    - property_name: Field to sort by
    - order: "asc" or "desc"
    - top: Maximum number of results to return
    """
    base_url, _ = get_odata_creds(metadata)
    order_str = f"{property_name} {order}"

    url = f"{base_url.rstrip('/')}/{entity_set}?$orderby={order_str}&$top={top}"
    headers = {"Accept": "application/json"}
    resp = make_odata_request(url, headers=headers, metadata=metadata)

    if not resp.ok:
        raise Exception(f"❌ Sort failed: {resp.status_code} - {resp.text}")

    return resp.json().get("d", {}).get("results", [])



@mcp.tool(name="odata_filter_and_sort")
def odata_filter_and_sort(
    metadata: dict,
    entity_set: str,
    filter_by: Optional[str] = None,      # e.g., "CurrencyCode eq 'USD'"
    sort_by: Optional[str] = None,        # e.g., "GrossAmount desc"
    top: int = 50,
) -> List[Dict]:
    """
    Filters and sorts an OData entity set with optional criteria.
    
    Parameters:
    - metadata: Contains 'base_url' and optional 'username', 'password'
    - entity_set: Name of the OData entity set
    - filter_by: Optional OData filter string
    - sort_by: Optional OData order-by string
    - top: Maximum number of results to return
    """
    base_url, _ = get_odata_creds(metadata)

    query_parts = []
    if filter_by:
        query_parts.append(f"$filter={filter_by}")
    if sort_by:
        query_parts.append(f"$orderby={sort_by}")
    query_parts.append(f"$top={top}")
    query_str = "&".join(query_parts)

    url = f"{base_url.rstrip('/')}/{entity_set}?{query_str}"
    headers = {"Accept": "application/json"}
    resp = make_odata_request(url, headers=headers, metadata=metadata)

    if not resp.ok:
        raise Exception(f"❌ Filter/sort failed: {resp.status_code} - {resp.text}")

    return resp.json().get("d", {}).get("results", [])



@mcp.tool(name="odata_count")
def count_entities(
    metadata: dict,
    entity_set: str,
    filter_expr: str = ""
) -> str:
    """
    Returns the count of entities in the specified OData entity set.
    
    Parameters:
    - metadata: Contains 'base_url' and optional 'username', 'password'
    - entity_set: Name of the OData entity set
    - filter_expr: Optional OData filter string (e.g., "Status eq 'Active'")
    """
    base_url, _ = get_odata_creds(metadata)
    params = {"$filter": filter_expr} if filter_expr else {}

    url = f"{base_url.rstrip('/')}/{entity_set}/$count"
    resp = make_odata_request(url, params=params, metadata=metadata)

    if resp.ok:
        return f"🔢 Count: {resp.text}"
    else:
        return f"❌ Failed to count: {resp.status_code} - {resp.text}"



def get_select_fields_excluding_binary(entity_set: str, metadata: dict) -> Optional[str]:
    """
    Returns a comma-separated list of non-binary fields for the given OData entity set.
    
    Uses metadata to fetch credentials and base URL.
    """
    base_url, _ = get_odata_creds(metadata)
    url = f"{base_url}/$metadata"

    resp = make_odata_request(url, metadata=metadata)
    if not resp.ok:
        return None

    tree = ET.fromstring(resp.text)
    ns = {"edm": "http://docs.oasis-open.org/odata/ns/edm"}

    # Find entity type name from entity set
    entity_type = next(
        (es.attrib["EntityType"].split(".")[-1]
         for es in tree.findall(".//edm:EntitySet", ns)
         if es.attrib.get("Name") == entity_set),
        None
    )
    if not entity_type:
        return None

    # Collect non-binary fields
    fields = [
        p.attrib["Name"]
        for et in tree.findall(f".//edm:EntityType[@Name='{entity_type}']", ns)
        for p in et.findall("edm:Property", ns)
        if "binary" not in p.attrib.get("Type", "").lower()
    ]
    return ",".join(fields) if fields else None


@mcp.tool(name="odata_metadata")
def get_metadata(metadata: dict) -> str:
    """
    Fetches and returns the OData $metadata XML as a string.

    Requires `metadata` containing at least the base_url, and optionally username/password for basic auth.
    """
    base_url, _ = get_odata_creds(metadata)
    url = f"{base_url}/$metadata"
    headers = {"Accept": "application/xml"}

    resp = make_odata_request(url, headers=headers, metadata=metadata)
    return truncate_response(resp.text) if resp.ok else f"❌ Failed to fetch metadata: {resp.status_code} - {resp.text}"


@mcp.tool(name="odata_top_values")
def top_values(
    metadata: dict,
    entity_set: str,
    field: str,
    top: int = 5
) -> str:
    """
    Returns the top unique values for a given field in the specified entity set.

    Requires `metadata` to contain the base_url (and optionally username/password for auth).
    """
    base_url, _ = get_odata_creds(metadata)
    url = f"{base_url}/{entity_set}?$select={field}&$top=100&$format=json"

    resp = make_odata_request(url, metadata=metadata)
    if not resp.ok:
        return f"❌ Failed to fetch values: {resp.status_code} - {resp.text}"
    
    try:
        results = [r.get(field) for r in resp.json().get("d", {}).get("results", []) if field in r]
        unique_values = list(dict.fromkeys(results))[:top]
        return (
            f"Top {top} unique values for '{field}':\n" + "\n".join(map(str, unique_values))
            if unique_values else "ℹ️ No values found."
        )
    except Exception as e:
        return f"❌ Failed to parse response: {str(e)}"



@mcp.tool(name="odata_list_entity_sets_and_relationships")
def list_entity_sets_and_relationships(
    metadata: dict
) -> Union[str, Dict[str, Union[List[str], Dict[str, List[str]]]]]:
    """
    Returns a structured summary of the OData service metadata.

    Specifically:
    - Lists all entity sets available in the service.
    - Maps each entity type to its scalar properties.
    - Extracts relationships between entity types (via navigation properties).

    This tool is useful for understanding the structure of an OData service
    and differs from `odata_metadata`, which returns raw XML.
    """
    base_url, _ = get_odata_creds(metadata)
    url = f"{base_url}/$metadata"
    headers = {"Accept": "application/xml"}
    resp = make_odata_request(url, headers=headers, metadata=metadata)

    if not resp.ok:
        return f"❌ Failed to fetch metadata: {resp.status_code} - {resp.text}"

    xml = resp.text
    try:
        # Extract namespaces
        events = ("start", "start-ns")
        ns_map = {}
        for event, elem in ET.iterparse(io.StringIO(xml), events):
            if event == "start-ns":
                prefix, uri = elem
                ns_map[prefix] = uri
            elif event == "start":
                break

        root = ET.fromstring(xml)
        entity_sets = {}
        entity_types = {}
        relationships: Dict[str, List[str]] = {}
        entity_properties: Dict[str, List[str]] = {}

        # Map of associations: name -> (from_type, to_type)
        association_map = {}

        # Build map of EntitySet -> EntityType
        for es in root.findall(".//{*}EntitySet"):
            name = es.attrib.get("Name")
            entity_type = es.attrib.get("EntityType", "").split('.')[-1]
            entity_sets[name] = entity_type

        # Collect entity properties and navigation property names
        for et in root.findall(".//{*}EntityType"):
            et_name = et.attrib["Name"]
            entity_types[et_name] = et
            props = [p.attrib.get("Name") for p in et.findall("{*}Property")]
            entity_properties[et_name] = props

        # Parse Associations to resolve relationship targets
        for assoc in root.findall(".//{*}Association"):
            assoc_name = assoc.attrib.get("Name")
            ends = assoc.findall("{*}End")
            if len(ends) == 2:
                from_type = ends[0].attrib.get("Type", "").split('.')[-1]
                to_type = ends[1].attrib.get("Type", "").split('.')[-1]
                association_map[assoc_name] = (from_type, to_type)

        # Now parse NavigationProperties and resolve their targets via association
        for et_name, et in entity_types.items():
            nav_targets = []
            for nav in et.findall("{*}NavigationProperty"):
                rel = nav.attrib.get("Relationship", "").split('.')[-1]
                from_type, to_type = association_map.get(rel, ("", ""))
                # Try both directions to see which is "to"
                if from_type and to_type:
                    if from_type == et_name:
                        nav_targets.append(to_type)
                    else:
                        nav_targets.append(from_type)
                else:
                    # fallback to inline Type if exists
                    type_attr = nav.attrib.get("Type", "").split('.')[-1].replace("Collection(", "").replace(")", "")
                    if type_attr:
                        nav_targets.append(type_attr)

            if nav_targets:
                relationships[et_name] = nav_targets

        return {
            "entity_sets": list(entity_sets.keys()),
            "relationships": relationships,
            "entity_properties": entity_properties
        }

    except Exception as e:
        return f"❌ Failed to parse metadata: {e}"


def extract_auto_expand_clause(entity_set: str, metadata: Optional[dict] = None) -> str:
    """
    Extracts the $expand clause for all navigation properties of the given entity set.
    Uses metadata to determine the base_url and perform the XML parsing.
    """
    base_url, _ = get_odata_creds(metadata)
    metadata_xml = get_metadata(metadata=metadata)
    tree = ET.fromstring(metadata_xml)

    ns = {
        "edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
        "edm": "http://schemas.microsoft.com/ado/2008/09/edm"
    }

    expand_paths = []

    for entity_container in tree.findall(".//edm:EntityContainer", ns):
        for es in entity_container.findall(f".//edm:EntitySet[@Name='{entity_set}']", ns):
            etype = es.get("EntityType").split(".")[-1]
            for et in tree.findall(f".//edm:EntityType[@Name='{etype}']", ns):
                for nav_prop in et.findall("edm:NavigationProperty", ns):
                    expand_paths.append(nav_prop.attrib["Name"])

    if expand_paths:
        return f"?$expand={','.join(expand_paths)}"
    return ""


@mcp.tool(name="odata_expand_or_walk")
def odata_expand_or_walk(
    entity_set: str,
    key: Optional[str] = None,
    metadata: Optional[dict] = None,
    base_url: str = DEFAULT_BASE_URL
) -> str:
    """
    Fetches an entity (optionally by key) and expands all navigation properties using $expand.
    Uses metadata to extract base_url and handle auth if needed.
    """
    base_url, _ = get_odata_creds(metadata)
    expand_clause = extract_auto_expand_clause(entity_set, metadata)
    key_part = f"({quote_key_if_needed(key)})" if key else ""
    connector = "&" if expand_clause else "?"
    url = f"{base_url.rstrip('/')}/{entity_set}{key_part}{expand_clause}{connector}$format=json"

    resp = make_odata_request(url, metadata=metadata)
    if not resp.ok:
        return f"❌ Failed to fetch entity: {resp.status_code} - {resp.text}"
    try:
        return truncate_response(resp.json())
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"




# @mcp.tool(name="apply_groupby_aggregate")
# def apply_groupby_aggregate(entity_set: str, groupby: str, aggregate: str, base_url: str = DEFAULT_BASE_URL) -> str:
#     """
#     Applies a groupby and aggregate operation to the specified entity set using OData $apply.
#     If $apply is not supported, performs manual aggregation as a fallback.
#     Aggregate string can be in formats like:
#       - sum(GrossAmount)
#       - sum(GrossAmount) as TotalRevenue
#       - GrossAmount with sum as TotalRevenue
#     """
#     apply_query = f"?$apply=groupby(({groupby}), aggregate({aggregate}))"
#     url = f"{base_url.rstrip('/')}/{entity_set}{apply_query}"
#     resp = make_odata_request(url, base_url=base_url)

#     if resp.ok:
#         try:
#             return truncate_response(resp.json())
#         except Exception as e:
#             return f"❌ Failed to parse JSON response: {str(e)}"

#     # fallback if $apply not supported
#     fallback_url = f"{base_url.rstrip('/')}/{entity_set}?$format=json"
#     resp = make_odata_request(fallback_url, base_url=base_url)
#     if not resp.ok:
#         return f"❌ Failed to fetch fallback: {resp.status_code} - {resp.text}"

#     try:
#         data = resp.json().get("d", {}).get("results", [])
#         if not data:
#             return "No data to aggregate."

#         # Accept any of these:
#         # - sum(GrossAmount)
#         # - sum(GrossAmount) as TotalRevenue
#         # - GrossAmount with sum as TotalRevenue

#         field, func, alias = None, None, None

#         match = re.match(r'(\w+)\s+with\s+(\w+)\s+as\s+(\w+)', aggregate.strip(), re.IGNORECASE)
#         if match:
#             field, func, alias = match.groups()
#         else:
#             match = re.match(r'(\w+)\((\w+)\)\s+as\s+(\w+)', aggregate.strip(), re.IGNORECASE)
#             if match:
#                 func, field, alias = match.groups()
#             else:
#                 match = re.match(r'(\w+)\((\w+)\)', aggregate.strip(), re.IGNORECASE)
#                 if match:
#                     func, field = match.groups()
#                     alias = f"{func}_{field}"  # fallback alias
#                 else:
#                     return f"❌ Invalid aggregate format: {aggregate}"

#         func = func.lower()

#         group_map = defaultdict(list)
#         for row in data:
#             group_key = row.get(groupby)
#             value = row.get(field)
#             if group_key is not None and isinstance(value, (int, float)):
#                 group_map[group_key].append(value)

#         result = []
#         for k, vals in group_map.items():
#             try:
#                 if func == "sum":
#                     agg_val = sum(vals)
#                 elif func == "average":
#                     agg_val = sum(vals) / len(vals)
#                 elif func == "count":
#                     agg_val = len(vals)
#                 else:
#                     return f"❌ Unsupported aggregation function: {func}"
#                 result.append({groupby: k, alias: agg_val})
#             except Exception:
#                 result.append({groupby: k, alias: "❌ Calculation error"})

#         return truncate_response({"results": result})
#     except Exception as e:
#         return f"❌ Manual aggregation failed: {str(e)}"

from typing import Optional, Dict, Union
from urllib.parse import quote
import json

@mcp.tool(name="apply_aggregate_related")
def odata_aggregate_related(
    entity_set: str,
    key: str,
    navigation_property: str,
    aggregation: str = "count",
    aggregation_field: Optional[str] = None,
    filter: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    metadata: Optional[Dict] = None,
) -> Union[int, float]:
    """
    Aggregates related entities using navigation properties.
    
    Supports aggregation functions such as count, sum, and average 
    on the related entity set accessible through a navigation property.

    Example: Count sales orders for a business partner with status != 'C'

    Parameters:
    - entity_set: The primary entity set name (e.g., 'BusinessPartnerSet')
    - key: The key of the entity instance (e.g., '0100000000')
    - navigation_property: Navigation property to the related entity set (e.g., 'ToSalesOrders')
    - aggregation: 'count' (default), 'sum', or 'average'
    - aggregation_field: Required for 'sum' and 'average'
    - filter: Optional OData $filter clause
    - headers: Optional headers (e.g., auth, Accept)
    - metadata: Required metadata containing 'base_url' and optional credentials

    Returns:
    - Count (int) or aggregated value (float)
    """
    base_url, auth = get_odata_creds(metadata)
    headers = headers or {}

    key_encoded = quote(key)
    nav_path = f"{entity_set}('{key_encoded}')/{navigation_property}"

    if aggregation.lower() == "count":
        url = f"{base_url}/{nav_path}/$count"
        params = {"$filter": filter} if filter else {}
        resp = make_odata_request(url, headers=headers, params=params, metadata=metadata)
        return int(resp.text)

    elif aggregation.lower() in {"sum", "average"}:
        if not aggregation_field:
            raise ValueError("aggregation_field is required for sum or average")

        agg_func = "sum" if aggregation.lower() == "sum" else "average"
        apply_parts = []
        if filter:
            apply_parts.append(f"filter({filter})")
        apply_parts.append(f"groupby((),aggregate({aggregation_field} with {agg_func} as agg))")
        apply_clause = "/".join(apply_parts)

        url = f"{base_url}/{nav_path}"
        params = {"$apply": apply_clause}
        resp = make_odata_request(url, headers=headers, params=params, metadata=metadata)

        data = resp.json()
        results = data.get("d", {}).get("results") or data.get("value")
        if results and isinstance(results, list) and "agg" in results[0]:
            return results[0]["agg"]
        return 0.0

    else:
        raise ValueError("Unsupported aggregation: use count, sum, or average")




# @mcp.tool(name="apply_groupby_aggregate")
# def apply_groupby_aggregate(entity_set: str, groupby: str, aggregate: str, base_url: str = DEFAULT_BASE_URL) -> str:
#     apply_query = f"?$apply=groupby(({groupby}), aggregate({aggregate}))"
#     url = f"{base_url.rstrip('/')}/{entity_set}{apply_query}"
#     resp = make_odata_request(url, base_url=base_url)

#     if resp.ok:
#         try:
#             return truncate_response(resp.json())
#         except Exception as e:
#             return f"❌ Failed to parse JSON response: {str(e)}"

#     # fallback if $apply not supported
#     headers = {"Accept": "application/json"}
#     data = []
#     next_url = f"{base_url.rstrip('/')}/{entity_set}?$format=json"
#     while next_url:
#         resp = make_odata_request(next_url, headers=headers, base_url=base_url)
#         if not resp.ok:
#             return f"❌ Failed to fetch fallback: {resp.status_code} - {resp.text}"

#         json_data = resp.json()
#         if "d" in json_data:
#             results = json_data["d"].get("results", [])
#             next_url = json_data["d"].get("__next")
#         elif "value" in json_data:
#             results = json_data.get("value", [])
#             next_url = json_data.get("@odata.nextLink")
#         else:
#             return "❌ Unexpected response format"

#         data.extend(results)

#     if not data:
#         return "No data to aggregate."

#     # Parse aggregate string
#     field, func, alias = None, None, None
#     match = re.match(r'(\w+)\s+with\s+(\w+)\s+as\s+(\w+)', aggregate.strip(), re.IGNORECASE)
#     if match:
#         field, func, alias = match.groups()
#     else:
#         match = re.match(r'(\w+)\((\w+)\)\s+as\s+(\w+)', aggregate.strip(), re.IGNORECASE)
#         if match:
#             func, field, alias = match.groups()
#         else:
#             match = re.match(r'(\w+)\((\w+)\)', aggregate.strip(), re.IGNORECASE)
#             if match:
#                 func, field = match.groups()
#                 alias = f"{func}_{field}"
#             else:
#                 return f"❌ Invalid aggregate format: {aggregate}"

#     func = func.lower()

#     from collections import defaultdict
#     group_map = defaultdict(list)
#     for row in data:
#         group_key = row.get(groupby)
#         value = row.get(field)
#         if group_key is not None and isinstance(value, (int, float)):
#             group_map[group_key].append(value)

#     result = []
#     for k, vals in group_map.items():
#         try:
#             if func == "sum":
#                 agg_val = sum(vals)
#             elif func == "average":
#                 agg_val = sum(vals) / len(vals)
#             elif func == "count":
#                 agg_val = len(vals)
#             else:
#                 return f"❌ Unsupported aggregation function: {func}"
#             result.append({groupby: k, alias: agg_val})
#         except Exception:
#             result.append({groupby: k, alias: "❌ Calculation error"})

#     return truncate_response({"results": result})





@mcp.tool(name="odata_get_navigation_paths")
def odata_get_navigation_paths(
    entity_set: str,
    metadata: Optional[dict] = None,
    base_url: str = DEFAULT_BASE_URL
) -> str:
    """
    Returns the navigation property paths (i.e., relationships) for the given entity set 
    using the OData $metadata document.
    
    This tool helps identify which related entities can be expanded using $expand 
    in OData queries.
    """
    # base_url, _ = get_odata_creds(metadata)
    metadata_xml = get_metadata(metadata=metadata)
    tree = ET.fromstring(metadata_xml)

    ns = {
        "edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
        "edm": "http://schemas.microsoft.com/ado/2008/09/edm"
    }

    paths = []

    for container in tree.findall(".//edm:EntityContainer", ns):
        for es in container.findall(f".//edm:EntitySet[@Name='{entity_set}']", ns):
            etype = es.get("EntityType").split(".")[-1]
            for et in tree.findall(f".//edm:EntityType[@Name='{etype}']", ns):
                for nav in et.findall("edm:NavigationProperty", ns):
                    paths.append(nav.attrib["Name"])

    return json.dumps({"navigation_paths": paths})


# ─── App Setup ───────────────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")

routes = [
    Mount("/mcp-server", app=mcp_app),
]

app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    print(f"🔌 Starting OData MCP on http://localhost:{PORT}/mcp-server")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
