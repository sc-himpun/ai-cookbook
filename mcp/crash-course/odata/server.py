import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional, Any, List
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
from urllib.parse import quote
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
from collections import defaultdict
import statistics
from typing import List, Optional

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()

PORT = int(os.getenv("ODATA_MCP_PORT", "8016"))

# Optional auth/token store (for future SAP integrations)
user_tokens: Dict[str, Dict] = {}

# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("odata-mcp")
MAX_CHARS = 5000  # Or tweak based on your LLM/token budget

DEFAULT_BASE_URL = "https://services.odata.org/V4/Northwind/Northwind.svc"

def truncate_response(data: Any) -> str:
    stringified = str(data)
    if len(stringified) > MAX_CHARS:
        return stringified[:MAX_CHARS] + f"\n...(truncated to {MAX_CHARS} chars)"
    return stringified


@mcp.tool(name="odata_search")
def search_entity(
    entity_set: str,
    email: Optional[str] = None,
    filter_expr: str = "",
    top: int = 5,
    select_fields: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL
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
def list_entity_sets(base_url: str = DEFAULT_BASE_URL) -> str:
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
def describe_entity_set(entity_set: str, base_url: str = DEFAULT_BASE_URL) -> str:
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
    base_url: str = DEFAULT_BASE_URL,
    email: Optional[str] = None,
) -> str:
    """
    Fetch a specific record from an OData entity set using its key.
    Skips binary fields (e.g., BLOBs) automatically.
    """
    if "services.odata.org" not in base_url and email not in user_tokens:
        return "❌ Email not authorized for private OData service."

    headers = {}
    if email and email in user_tokens:
        token = user_tokens[email].get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    # Get non-binary fields to filter the response
    select_fields = get_select_fields_excluding_binary(entity_set, base_url)
    select_query = f"?$select={select_fields}&$format=json" if select_fields else "?$format=json"

    url = f"{base_url.rstrip('/')}/{entity_set}({key}){select_query}"

    resp = requests.get(url, headers=headers)
    if not resp.ok:
        return f"❌ Failed to fetch entity: {resp.status_code} - {resp.text}"

    try:
        data = resp.json()
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"

    return truncate_response(data)




@mcp.tool(name="odata_count")
def count_entities(
    entity_set: str,
    filter_expr: str = "",
    base_url: str = DEFAULT_BASE_URL
) -> str:
    """Count the number of records in an entity set, optionally filtered."""
    params = {"$format": "json"}
    if filter_expr:
        params["$filter"] = filter_expr

    headers = {
    "Accept": "application/json;odata.metadata=minimal"
    }
    url = f"{base_url.rstrip('/')}/{entity_set}/$count"
    resp = requests.get(url, params=params, headers=headers)
    if not resp.ok:
        return f"❌ Failed to count entities: {resp.status_code} - {resp.text}"
    return f"🔢 Count: {resp.text}"




def get_select_fields_excluding_binary(entity_set: str, base_url: str) -> Optional[str]:
    import xml.etree.ElementTree as ET
    metadata_url = f"{base_url.rstrip('/')}/$metadata"
    resp = requests.get(metadata_url)
    if not resp.ok:
        return None

    tree = ET.fromstring(resp.text)
    ns = {"edm": "http://docs.oasis-open.org/odata/ns/edm"}

    # Find entity type for this entity set
    entity_type = None
    for container in tree.findall(".//edm:EntityContainer", ns):
        for es in container.findall("edm:EntitySet", ns):
            if es.attrib.get("Name") == entity_set:
                entity_type = es.attrib.get("EntityType").split(".")[-1]
                break

    if not entity_type:
        return None

    # Get all non-binary fields
    fields = []
    for et in tree.findall(".//edm:EntityType", ns):
        if et.attrib.get("Name") == entity_type:
            for prop in et.findall("edm:Property", ns):
                if "binary" not in prop.attrib.get("Type", "").lower():
                    fields.append(prop.attrib["Name"])
            break

    if not fields:
        print(f"⚠️ No non-binary fields found for entity type: {entity_type}")
        return None

    return ",".join(fields)





def extract_auto_expand_clause(metadata_xml: str, entity_set: str, depth: int = 1) -> str:
    ns = {'edmx': 'http://docs.oasis-open.org/odata/ns/edmx',
          'edm': 'http://docs.oasis-open.org/odata/ns/edm'}

    tree = ET.fromstring(metadata_xml)
    nav_map = {}

    for entity_type in tree.findall(".//edm:EntityType", ns):
        type_name = entity_type.attrib["Name"]
        nav_props = [nav.attrib["Name"] for nav in entity_type.findall("edm:NavigationProperty", ns)]
        nav_map[type_name] = nav_props

    # Map entity_set to entity_type
    container = tree.find(".//edm:EntityContainer", ns)
    set_to_type = {}
    for es in container.findall("edm:EntitySet", ns):
        set_to_type[es.attrib["Name"]] = es.attrib["EntityType"].split(".")[-1]

    def build_expand(entity_type: str, level: int) -> str:
        if level == 0 or entity_type not in nav_map:
            return ""
        children = nav_map[entity_type]
        expands = []
        for c in children:
            sub = build_expand(c, level - 1)
            if sub:
                expands.append(f"{c}($expand={sub})")
            else:
                expands.append(c)
        return ",".join(expands)

    root_entity_type = set_to_type.get(entity_set)
    if not root_entity_type:
        return ""

    return build_expand(root_entity_type, depth)


@mcp.tool(name="odata_expand_or_walk_navigation")
def odata_expand_or_walk(
    entity_set: str,
    expand_clause: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    key: Optional[str] = None,
    top: int = 5,
    auto_expand: bool = False,
    expand_depth: int = 1,
    email: Optional[str] = None,
    exclude_large_fields: bool = True
) -> str:
    """
    Unified OData navigator:
    - Expand with explicit clause OR auto-expand using $metadata up to N levels.
    - If `key` is provided: walk a single record, else expand across top N.
    Option 5: Auto-expand using metadata by setting auto_expand=True
    """
    if "services.odata.org" not in base_url and email not in user_tokens:
        return "❌ Email not authorized for private OData service."

    headers = {}
    if email and email in user_tokens:
        token = user_tokens[email].get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    # Step 1: Auto-expand if requested
    if auto_expand:
        metadata_url = f"{base_url.rstrip('/')}/$metadata"
        meta_resp = requests.get(metadata_url, headers=headers)
        if not meta_resp.ok:
            return f"❌ Failed to fetch metadata: {meta_resp.status_code}"
        expand_clause = extract_auto_expand_clause(meta_resp.text, entity_set, expand_depth)
        if not expand_clause:
            return "❌ Could not infer navigation structure from metadata."

    if not expand_clause:
        return "❌ Either `expand_clause` or `auto_expand=True` must be provided."

    # Step 2: Prepare request
    url = f"{base_url.rstrip('/')}/{entity_set}"
    if key:
        url += f"({key})"

    params = {
        "$expand": expand_clause,
        "$format": "json"
    }

    # Step 3: Optionally exclude large fields (like Picture)
    if exclude_large_fields:
        select_clause = get_select_fields_excluding_binary(entity_set, base_url)
        if select_clause:
            params["$select"] = select_clause

    # Step 4: If no key, add pagination
    if not key:
        params["$top"] = top

    resp = requests.get(url, headers=headers, params=params)
    if not resp.ok:
        return f"❌ Failed to fetch data: {resp.status_code} - {resp.text}"

    try:
        data = resp.json()
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"

    # Step 5: Compact/truncate output
    if key:
        return truncate_response(data)
    else:
        results = data.get("value", [])
        compact = [
            {k: v for k, v in item.items() if not isinstance(v, dict)}
            for item in results
        ]
        return truncate_response(compact)



@mcp.tool(name="odata_groupby_aggregate")
def apply_groupby_aggregate(
    entity_set: str,
    groupby_fields: str,
    aggregate_expr: str,
    base_url: str = DEFAULT_BASE_URL
) -> str:
    """
    Run $apply=groupby(…) aggregate on an entity set.
    Falls back to Python-side aggregation if $apply is unsupported.
    Supports multiple group-by fields like "Region, Country".
    Supported fallback aggregations: count, sum, avg, min, max
    """
    try:
        apply_expr = f"groupby(({groupby_fields}), aggregate({aggregate_expr}))"
        encoded_apply = quote(apply_expr, safe="(),= ")
        url = f"{base_url.rstrip('/')}/{entity_set}?$apply={encoded_apply}&$format=json"

        resp = requests.get(url)
        if resp.ok:
            results = resp.json().get("value", [])
            if not results:
                return "No results found."
            return truncate_response(results)
        elif resp.status_code == 400 and "$apply" in resp.text.lower():
            pass  # Fall back to manual
        else:
            return f"❌ Failed to run $apply: {resp.status_code} - {resp.text}"
    except Exception as e:
        return f"❌ Error during $apply: {str(e)}"

    # Fallback logic
    fallback_url = f"{base_url.rstrip('/')}/{entity_set}?$format=json"
    fallback_resp = requests.get(fallback_url)
    if not fallback_resp.ok:
        return f"❌ Fallback fetch failed: {fallback_resp.status_code} - {fallback_resp.text}"

    try:
        items = fallback_resp.json().get("value", [])
    except Exception as e:
        return f"❌ Failed to parse fallback JSON: {str(e)}"

    if not items:
        return "No results in fallback."

    # Parse aggregation expression: "Field with aggfunc as Alias"
    try:
        parts = [x.strip() for x in aggregate_expr.split("with")]
        field = parts[0]
        func_part = parts[1].split("as")
        func = func_part[0].strip().lower()
        alias = func_part[1].strip()
    except Exception:
        return "❌ Invalid aggregation expression. Format: 'Field with sum as Total'"

    # Handle multiple group-by fields
    group_fields = [f.strip() for f in groupby_fields.split(",")]
    groupby = defaultdict(list)
    for item in items:
        key = tuple(item.get(f) for f in group_fields)
        if any(k is None for k in key):
            continue
        groupby[key].append(item)

    results = []
    for group_key, group_items in groupby.items():
        values = [i.get(field) for i in group_items if isinstance(i.get(field), (int, float))]
        if func == "sum":
            agg_val = sum(values)
        elif func == "avg":
            agg_val = statistics.mean(values) if values else 0
        elif func == "min":
            agg_val = min(values) if values else None
        elif func == "max":
            agg_val = max(values) if values else None
        elif func == "$count()" or func == "count":
            agg_val = len(group_items)
        else:
            return f"❌ Unsupported aggregation: {func}"

        row = {alias: agg_val}
        for i, field_name in enumerate(group_fields):
            row[field_name] = group_key[i]
        results.append(row)

    return truncate_response(results)



@mcp.tool(name="odata_get_navigation_paths")
def odata_get_navigation_paths(
    entity_set: str,
    base_url: str = DEFAULT_BASE_URL,
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
def get_metadata(base_url: str = DEFAULT_BASE_URL) -> str:
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
