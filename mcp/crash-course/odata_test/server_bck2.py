import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional, Any
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
from urllib.parse import quote
import requests
import xml.etree.ElementTree as ET
from typing import List, Optional

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()

PORT = int(os.getenv("ODATA_MCP_PORT", "8016"))

# Optional auth/token store (for future SAP integrations)
user_tokens: Dict[str, Dict] = {}

# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("odata-mcp")
MAX_CHARS = 5000  # Or tweak based on your LLM/token budget

def truncate_response(data: Any) -> str:
    stringified = str(data)
    if len(stringified) > MAX_CHARS:
        return stringified[:MAX_CHARS] + f"\n...(truncated to {MAX_CHARS} chars)"
    return stringified


@mcp.tool(name="odata_search")
def search_entity(
    email: Optional[str],
    entity_set: str,
    filter_expr: str = "",
    top: int = 5,
    select_fields: Optional[str] = None,
    base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc"
) -> str:
    """
    Search a generic OData service with optional filter (e.g., Products, Orders).
    Supports optional $select to reduce result size.
    """
    if "services.odata.org" not in base_url and email not in user_tokens:
        return "❌ Email not authorized for private OData service."

    headers = {}
    if email and email in user_tokens:
        token = user_tokens[email].get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    url = f"{base_url.rstrip('/')}/{entity_set}"
    params = {"$top": top, "$format": "json"}
    if filter_expr:
        params["$filter"] = filter_expr
    if select_fields:
        params["$select"] = select_fields

    resp = requests.get(url, headers=headers, params=params)
    if not resp.ok:
        return f"❌ Failed to fetch data: {resp.status_code} - {resp.text}"

    try:
        data = resp.json()
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"

    results = data.get("value", [])
    if not results:
        return "No results found."

    return truncate_response([
        {k: v for k, v in item.items() if not isinstance(v, dict)}
        for item in results[:top]
    ])



@mcp.tool(name="odata_list_entity_sets")
def list_entity_sets(base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc") -> str:
    """List all entity sets (tables) available in the OData service."""
    url = f"{base_url.rstrip('/')}/$metadata"
    resp = requests.get(url)
    if not resp.ok:
        return f"❌ Failed to fetch metadata: {resp.status_code}"

    from xml.etree import ElementTree as ET
    root = ET.fromstring(resp.text)
    ns = {'edmx': 'http://docs.oasis-open.org/odata/ns/edmx', 'edm': 'http://docs.oasis-open.org/odata/ns/edm'}

    entity_sets = []
    for container in root.findall(".//edm:EntityContainer", ns):
        for es in container.findall("edm:EntitySet", ns):
            name = es.attrib.get("Name")
            entity_type = es.attrib.get("EntityType")
            entity_sets.append(f"{name} → {entity_type}")

    # return "\n".join(entity_sets) if entity_sets else "No entity sets found."
    return truncate_response("\n".join(entity_sets)) if entity_sets else "No entity sets found."


@mcp.tool(name="odata_describe_entity_set")
def describe_entity_set(entity_set: str, base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc") -> str:
    """Describe the fields/columns of an entity set (like table schema)."""
    url = f"{base_url.rstrip('/')}/$metadata"
    resp = requests.get(url)
    if not resp.ok:
        return f"❌ Failed to fetch metadata: {resp.status_code}"

    from xml.etree import ElementTree as ET
    root = ET.fromstring(resp.text)
    ns = {'edmx': 'http://docs.oasis-open.org/odata/ns/edmx', 'edm': 'http://docs.oasis-open.org/odata/ns/edm'}

    entity_type = None
    for es in root.findall(".//edm:EntitySet", ns):
        if es.attrib.get("Name") == entity_set:
            entity_type = es.attrib.get("EntityType").split('.')[-1]
            break

    if not entity_type:
        return f"Entity set '{entity_set}' not found."

    fields = []
    for et in root.findall(f".//edm:EntityType[@Name='{entity_type}']", ns):
        for prop in et.findall("edm:Property", ns):
            pname = prop.attrib.get("Name")
            ptype = prop.attrib.get("Type")
            fields.append(f"{pname}: {ptype}")

    # return f"Schema for '{entity_set}':\n" + "\n".join(fields) if fields else "No properties found."
    return truncate_response(f"Schema for '{entity_set}':\n" + "\n".join(fields)) if fields else "No properties found."


@mcp.tool(name="odata_entity_by_key")
def get_entity_by_key(
    entity_set: str,
    key: str,
    base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc",
    email: Optional[str] = None,
) -> str:
    """
    Fetch a specific record from an OData entity set using its key.
    Example: get employee 5 from Employees set.
    """
    if "services.odata.org" not in base_url and email not in user_tokens:
        return "❌ Email not authorized for private OData service."

    headers = {}
    if email and email in user_tokens:
        token = user_tokens[email].get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    url = f"{base_url.rstrip('/')}/{entity_set}({key})?$format=json"
    resp = requests.get(url, headers=headers)
    if not resp.ok:
        return f"❌ Failed to fetch entity: {resp.status_code} - {resp.text}"

    # return str(resp.json())[:2000] + "\n...(truncated)"
    try:
        data = resp.json()
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"
    
    return truncate_response(data)



@mcp.tool(name="odata_count")
def count_entities(
    entity_set: str,
    filter_expr: str = "",
    base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc"
) -> str:
    """Count the number of records in an entity set, optionally filtered."""
    params = {"$format": "json"}
    if filter_expr:
        params["$filter"] = filter_expr

    url = f"{base_url.rstrip('/')}/{entity_set}/$count"
    resp = requests.get(url, params=params)
    if not resp.ok:
        return f"❌ Failed to count entities: {resp.status_code} - {resp.text}"
    return f"🔢 Count: {resp.text}"


@mcp.tool(name="odata_expand_or_walk_navigation")
def odata_expand_or_walk(
    entity_set: str,
    expand_clause: str,
    base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc",
    key: Optional[str] = None,
    top: int = 5,
    select_fields: Optional[str] = None,
    email: Optional[str] = None,
) -> str:
    """
    Unified navigation tool for OData:
    - If `key` is provided, walks navigation chain from a specific record.
    - Else, expands navigation on top-N records.
    Supports optional $select to reduce response size.
    """
    if "services.odata.org" not in base_url and email not in user_tokens:
        return "❌ Email not authorized for private OData service."

    headers = {}
    if email and email in user_tokens:
        token = user_tokens[email].get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    if key:
        url = f"{base_url.rstrip('/')}/{entity_set}({key})"
        params = {"$expand": expand_clause, "$format": "json"}
    else:
        url = f"{base_url.rstrip('/')}/{entity_set}"
        params = {"$top": top, "$expand": expand_clause, "$format": "json"}

    if select_fields:
        params["$select"] = select_fields

    resp = requests.get(url, headers=headers, params=params)
    if not resp.ok:
        return f"❌ Failed to fetch data: {resp.status_code} - {resp.text}"

    try:
        data = resp.json()
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"
    
    if key:
        return truncate_response(data)  # Single entity
    else:
        results = data.get("value", [])
        compact = [
            {k: v for k, v in item.items() if not isinstance(v, dict)}
            for item in results[:top]
        ]
        return truncate_response(compact)




@mcp.tool(name="odata_groupby_aggregate")
def apply_groupby_aggregate(
    entity_set: str,
    groupby_fields: str,
    aggregate_expr: str,
    base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc"
) -> str:
    """
    Run $apply=groupby(…) aggregate on an entity set.
    Example: groupby_fields="Region", aggregate_expr="Amount with sum as TotalSales"
    """
    apply_expr = f"groupby(({groupby_fields}), aggregate({aggregate_expr}))"
    encoded_apply = quote(apply_expr, safe="(),= ")

    url = f"{base_url.rstrip('/')}/{entity_set}?$apply={encoded_apply}&$format=json"
    resp = requests.get(url)

    if not resp.ok:
        return f"❌ Failed to run $apply: {resp.status_code} - {resp.text}"

    try:
        results = resp.json().get("value", [])
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"
    
    if not results:
        return "No results found."

    # return "\n---\n".join(str(item) for item in results)
    return truncate_response(results)


import requests
import xml.etree.ElementTree as ET
from typing import List, Optional

@mcp.tool(name="odata_get_navigation_paths")
def odata_get_navigation_paths(
    entity_set: str,
    base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc",
    email: Optional[str] = None,
) -> List[str]:
    """
    Parses OData $metadata and lists valid navigation paths for a given entity set.
    Example: odata_get_navigation_paths("Orders") → ["Customer", "Employee", "Order_Details/Product"]
    """
    headers = {}
    if email and email in user_tokens:
        token = user_tokens[email].get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    metadata_url = f"{base_url.rstrip('/')}/$metadata"
    resp = requests.get(metadata_url, headers=headers)
    if not resp.ok:
        return [f"❌ Failed to fetch metadata: {resp.status_code} - {resp.text}"]

    try:
        root = ET.fromstring(resp.text)
    except Exception as e:
        return [f"❌ Failed to parse metadata XML: {str(e)}"]

    # XML Namespaces
    ns = {'edm': 'http://docs.oasis-open.org/odata/ns/edm'}

    # Step 1: Map EntitySet → EntityType
    entity_type_map = {}
    for container in root.findall(".//edm:EntityContainer", ns):
        for es in container.findall("edm:EntitySet", ns):
            name = es.attrib["Name"]
            type_ = es.attrib["EntityType"].split(".")[-1]
            entity_type_map[name] = type_

    if entity_set not in entity_type_map:
        return [f"❌ EntitySet '{entity_set}' not found in $metadata."]

    target_entity_type = entity_type_map[entity_set]

    # Step 2: Find NavigationProperties for this EntityType
    navigation_paths = []

    def extract_paths(entity_type, prefix="", depth=0, max_depth=3):
        if depth > max_depth:
            return
        for et in root.findall(".//edm:EntityType", ns):
            if et.attrib.get("Name") == entity_type:
                for nav in et.findall("edm:NavigationProperty", ns):
                    nav_name = nav.attrib["Name"]
                    full_path = f"{prefix}{nav_name}"
                    navigation_paths.append(full_path)

                    # Recurse into target entity type if available
                    to_type = nav.attrib.get("Type", "").split(".")[-1]
                    if to_type != entity_type:  # avoid infinite loop
                        extract_paths(to_type, prefix=full_path + "/", depth=depth + 1)

    extract_paths(target_entity_type)

    return sorted(navigation_paths) or [f"ℹ️ No navigation paths found for {entity_set}."]



@mcp.tool(name="odata_metadata")
def get_metadata(base_url: str = "https://services.odata.org/V4/Northwind/Northwind.svc") -> str:
    """Fetch the $metadata XML from the OData service. For northwind dataset, base_url is https://services.odata.org/V4/Northwind/Northwind.svc"""
    url = f"{base_url.rstrip('/')}/$metadata"
    resp = requests.get(url)
    if not resp.ok:
        return f"❌ Failed to fetch metadata: {resp.status_code}"
    # return resp.text[:2000] + "\n...(truncated)"
    return truncate_response(resp.text)





@mcp.tool(name="odata_list_authorized_accounts")
def list_accounts() -> str:
    if not user_tokens:
        return "No authorized accounts."
    return "\n".join(user_tokens.keys())

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
