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


# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
PORT = int(os.getenv("ODATA_MCP_PORT", "8016"))
SAP_USERNAME = os.getenv("SAP_USERNAME")
SAP_PASSWORD = os.getenv("SAP_PASSWORD")

# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("odata-mcp")
MAX_CHARS = 8000

# DEFAULT_BASE_URL = "https://services.odata.org/V4/Northwind/Northwind.svc"
DEFAULT_BASE_URL = "https://sapes5.sapdevcenter.com/sap/opu/odata/IWBEP/GWSAMPLE_BASIC"


def get_entity_set_for_type(entity_type: str, metadata: dict) -> str:
    for entity_set, etype in metadata.get("entity_sets", {}).items():
        if etype == entity_type:
            return entity_set
    return entity_type  # fallback (assumes it's already an entity set)

def get_entity_type(entity_set: str, metadata: dict) -> str:
    return metadata.get("entity_sets", {}).get(entity_set, entity_set.rstrip("Set"))


@mcp.resource(uri="/metadata/{base_url}", name="odata_metadata_parsed")
def odata_metadata_parsed(base_url: str) -> Dict:
    """Parses OData $metadata and returns structured mappings."""
    url = f"{base_url.rstrip('/')}/$metadata"
    headers = {"Accept": "application/xml"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    ns = {
        "edmx": "http://docs.oasis-open.org/odata/ns/edmx",
        "edm": "http://schemas.microsoft.com/ado/2009/11/edm",
    }

    schema = root.find(".//edm:Schema", ns)

    entity_type_props: Dict[str, list] = {}
    entity_sets: list = []
    entity_type_by_set: Dict[str, str] = {}
    relationships: Dict[str, Dict[str, str]] = {}

    # Extract entity types and their properties
    for et in schema.findall("edm:EntityType", ns):
        et_name = et.attrib["Name"]
        props = [p.attrib["Name"] for p in et.findall("edm:Property", ns)]
        entity_type_props[et_name] = props

        # Find relationships (NavigationProperty)
        nav_props = et.findall("edm:NavigationProperty", ns)
        if nav_props:
            relationships[et_name] = {
                np.attrib["Name"]: np.attrib["Type"].split(".")[-1]
                for np in nav_props
            }

    # Extract entity sets and map to types
    for container in schema.findall("edm:EntityContainer", ns):
        for es in container.findall("edm:EntitySet", ns):
            es_name = es.attrib["Name"]
            et_type = es.attrib["EntityType"].split(".")[-1]
            entity_sets.append(es_name)
            entity_type_by_set[es_name] = et_type

    return {
        "entity_sets": entity_sets,
        "entity_type_by_set": entity_type_by_set,
        "entity_properties": entity_type_props,
        "relationships": relationships,
    }

def truncate_response(data: Any) -> str:
    stringified = str(data)
    if len(stringified) > MAX_CHARS:
        return stringified[:MAX_CHARS] + f"\n...(truncated to {MAX_CHARS} chars)"
    return stringified

def make_odata_request(url, headers=None, params=None, base_url=DEFAULT_BASE_URL):
    headers = headers or {}

    # Choose correct Accept header based on endpoint
    if "Accept" not in headers:
        if url.endswith("/$metadata"):
            headers["Accept"] = "application/xml"
        elif url.endswith("/$count"):
            pass  # let server return plain text
        else:
            headers["Accept"] = "application/json"

    auth = (SAP_USERNAME, SAP_PASSWORD) if "sapes5.sapdevcenter.com" in base_url else None
    return requests.get(url, headers=headers, params=params or {}, auth=auth)




# @mcp.tool(name="odata_search")
# def search_entity_set(entity_set: str, base_url: str = DEFAULT_BASE_URL, metadata: Optional[dict] = None, top: int = 5) -> str:
#     if metadata:
#         entity_set = get_entity_set_for_type(entity_set, metadata)
    
#     url = f"{base_url.rstrip('/')}/{entity_set}?$top={top}"
#     headers = {"Accept": "application/json"}
#     resp = make_odata_request(url, headers=headers, base_url=base_url)
    
#     if not resp.ok:
#         return f"❌ Failed to fetch data: {resp.status_code} - {resp.text}"
    
#     data = resp.json().get("d", {}).get("results", [])
#     if not data:
#         return f"ℹ️ No records found in {entity_set}."

#     return truncate_response(json.dumps(data, indent=2))

@mcp.tool(name="odata_search")
def search_entity_set(entity_set: str, field: str, value: str, odata_metadata_parsed: dict = None, base_url: str = DEFAULT_BASE_URL) -> str:
    entity_sets = odata_metadata_parsed.get("entity_sets", {})
    entity_type = entity_sets.get(entity_set, entity_set.rstrip("Set"))
    
    filter_expr = f"{field} eq '{value}'"
    url = f"{base_url}/{entity_set}?$filter={filter_expr}"
    resp = make_odata_request(url, base_url=base_url)
    if not resp.ok:
        return f"❌ Failed to fetch: {resp.status_code} - {resp.text}"
    
    results = resp.json().get("d", {}).get("results", [])
    if not results:
        return f"ℹ️ No matches found for `{field} eq '{value}'` in `{entity_set}`."
    
    return truncate_response(json.dumps(results, indent=2))




@mcp.tool(name="odata_describe_entity_set")
def describe_entity_set(entity_set: str, metadata: dict = None) -> str:
    entity_sets = metadata.get("entity_sets", {})
    entity_properties = metadata.get("entity_properties", {})
    
    entity_type = entity_sets.get(entity_set, entity_set.rstrip("Set"))
    props = entity_properties.get(entity_type)

    if not props:
        return f"ℹ️ No properties found for '{entity_type}'."
    return truncate_response(f"Schema for '{entity_set}':\n" + "\n".join(props))



def quote_key_if_needed(key: str) -> str:
    if key.isdigit() or key.startswith("("):  # numeric or composite key
        return key
    if not (key.startswith("'") and key.endswith("'")):
        return f"'{key}'"
    return key


@mcp.tool(name="odata_entity_by_key")
def get_entity_by_key(entity_set: str, key: str, base_url: str = DEFAULT_BASE_URL) -> str:
    key = quote_key_if_needed(key)
    select_fields = get_select_fields_excluding_binary(entity_set, base_url)
    select_query = f"?$select={select_fields}&$format=json" if select_fields else "?$format=json"
    url = f"{base_url.rstrip('/')}/{entity_set}({key}){select_query}"
    resp = make_odata_request(url, base_url=base_url)
    if not resp.ok:
        return f"❌ Failed to fetch entity: {resp.status_code} - {resp.text}"
    try:
        return truncate_response(resp.json())
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"


@mcp.tool(name="odata_count")
def count_entities(entity_set: str, filter_expr: str = "", base_url: str = DEFAULT_BASE_URL) -> str:
    params = {}
    if filter_expr:
        params["$filter"] = filter_expr
    url = f"{base_url.rstrip('/')}/{entity_set}/$count"
    resp = make_odata_request(url, params=params, base_url=base_url)
    return f"🔢 Count: {resp.text}" if resp.ok else f"❌ Failed to count: {resp.status_code} - {resp.text}"


def get_select_fields_excluding_binary(entity_set: str, base_url: str) -> Optional[str]:
    url = f"{base_url.rstrip('/')}/$metadata"
    resp = make_odata_request(url, base_url=base_url)
    if not resp.ok:
        return None
    tree = ET.fromstring(resp.text)
    ns = {"edm": "http://docs.oasis-open.org/odata/ns/edm"}
    entity_type = next((es.attrib["EntityType"].split(".")[-1] for es in tree.findall(".//edm:EntitySet", ns) if es.attrib.get("Name") == entity_set), None)
    if not entity_type:
        return None
    fields = [p.attrib["Name"] for et in tree.findall(".//edm:EntityType[@Name='%s']" % entity_type, ns) for p in et.findall("edm:Property", ns) if "binary" not in p.attrib.get("Type", "").lower()]
    return ",".join(fields) if fields else None

@mcp.tool(name="odata_metadata")
def get_metadata(base_url: str = DEFAULT_BASE_URL) -> str:
    url = f"{base_url.rstrip('/')}/$metadata"
    headers = {"Accept": "application/xml"}
    resp = make_odata_request(url, headers=headers, base_url=base_url)
    return truncate_response(resp.text) if resp.ok else f"❌ Failed to fetch metadata: {resp.status_code} - {resp.text}"

@mcp.tool(name="odata_top_values")
def top_values(entity_set: str, field: str, top: int = 5, base_url: str = DEFAULT_BASE_URL) -> str:
    url = f"{base_url.rstrip('/')}/{entity_set}?$select={field}&$top=100&$format=json"
    resp = make_odata_request(url, base_url=base_url)
    if not resp.ok:
        return f"❌ Failed to fetch values: {resp.status_code} - {resp.text}"
    try:
        results = [r.get(field) for r in resp.json().get("d", {}).get("results", []) if field in r]
        unique_values = list(dict.fromkeys(results))[:top]
        return f"Top {top} unique values for '{field}':\n" + "\n".join(map(str, unique_values)) if unique_values else "ℹ️ No values found."
    except Exception as e:
        return f"❌ Failed to parse response: {str(e)}"


# @mcp.tool(name="odata_list_entity_sets_and_relationships")
# def list_entity_sets(base_url: str = DEFAULT_BASE_URL) -> Union[str, Dict[str, Union[List[str], Dict[str, List[str]]]]]:
#     """
#     Lists entity sets, relationships, and properties from the OData $metadata document.
#     Supports both inline Type and external Association style navigation.
#     """
#     url = f"{base_url.rstrip('/')}/$metadata"
#     headers = {"Accept": "application/xml"}
#     resp = make_odata_request(url, headers=headers, base_url=base_url)
#     if not resp.ok:
#         return f"❌ Failed to fetch metadata: {resp.status_code} - {resp.text}"

#     xml = resp.text
#     try:
#         # Extract namespaces
#         events = ("start", "start-ns")
#         ns_map = {}
#         for event, elem in ET.iterparse(io.StringIO(xml), events):
#             if event == "start-ns":
#                 prefix, uri = elem
#                 ns_map[prefix] = uri
#             elif event == "start":
#                 break

#         root = ET.fromstring(xml)
#         entity_sets = {}
#         entity_types = {}
#         relationships: Dict[str, List[str]] = {}
#         entity_properties: Dict[str, List[str]] = {}

#         # Map of associations: name -> (from_type, to_type)
#         association_map = {}

#         # Build map of EntitySet -> EntityType
#         for es in root.findall(".//{*}EntitySet"):
#             name = es.attrib.get("Name")
#             entity_type = es.attrib.get("EntityType", "").split('.')[-1]
#             entity_sets[name] = entity_type

#         # Collect entity properties and navigation property names
#         for et in root.findall(".//{*}EntityType"):
#             et_name = et.attrib["Name"]
#             entity_types[et_name] = et
#             props = [p.attrib.get("Name") for p in et.findall("{*}Property")]
#             entity_properties[et_name] = props

#         # Parse Associations to resolve relationship targets
#         for assoc in root.findall(".//{*}Association"):
#             assoc_name = assoc.attrib.get("Name")
#             ends = assoc.findall("{*}End")
#             if len(ends) == 2:
#                 from_type = ends[0].attrib.get("Type", "").split('.')[-1]
#                 to_type = ends[1].attrib.get("Type", "").split('.')[-1]
#                 association_map[assoc_name] = (from_type, to_type)

#         # Now parse NavigationProperties and resolve their targets via association
#         for et_name, et in entity_types.items():
#             nav_targets = []
#             for nav in et.findall("{*}NavigationProperty"):
#                 rel = nav.attrib.get("Relationship", "").split('.')[-1]
#                 from_type, to_type = association_map.get(rel, ("", ""))
#                 # Try both directions to see which is "to"
#                 if from_type and to_type:
#                     if from_type == et_name:
#                         nav_targets.append(to_type)
#                     else:
#                         nav_targets.append(from_type)
#                 else:
#                     # fallback to inline Type if exists
#                     type_attr = nav.attrib.get("Type", "").split('.')[-1].replace("Collection(", "").replace(")", "")
#                     if type_attr:
#                         nav_targets.append(type_attr)

#             if nav_targets:
#                 relationships[et_name] = nav_targets

#         return {
#             "entity_sets": list(entity_sets.keys()),
#             "relationships": relationships,
#             "entity_properties": entity_properties
#         }

#     except Exception as e:
#         return f"❌ Failed to parse metadata: {e}"

@mcp.tool(name="odata_list_entity_sets_and_relationships")
def list_entity_sets_and_relationships(metadata: dict = None) -> str:
    entity_sets = metadata.get("entity_sets", {})
    relationships = metadata.get("relationships", {})
    
    lines = []
    for es, et in entity_sets.items():
        rels = relationships.get(et, [])
        rel_str = ", ".join(rels) if rels else "-"
        lines.append(f"- {es} (type: {et}) → [{rel_str}]")
    
    return truncate_response("📦 Available entity sets and relationships:\n" + "\n".join(lines))



def extract_auto_expand_clause(entity_set: str, base_url: str = DEFAULT_BASE_URL) -> str:
    metadata = get_metadata(base_url)
    tree = ET.fromstring(metadata)
    ns = {"edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
          "edm": "http://schemas.microsoft.com/ado/2008/09/edm"}

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
def odata_expand_or_walk(entity_set: str, key: Optional[str] = None, base_url: str = DEFAULT_BASE_URL) -> str:
    expand_clause = extract_auto_expand_clause(entity_set, base_url)
    key_part = f"('{key}')" if key else ""
    url = f"{base_url.rstrip('/')}/{entity_set}{key_part}{expand_clause}&$format=json"
    
    resp = make_odata_request(url, base_url=base_url)
    if not resp.ok:
        return f"❌ Failed to fetch entity: {resp.status_code} - {resp.text}"
    try:
        return truncate_response(resp.json())
    except Exception as e:
        return f"❌ Failed to parse JSON response: {str(e)}"



from collections import defaultdict
import re

@mcp.tool(name="apply_groupby_aggregate")
def apply_groupby_aggregate(entity_set: str, groupby: str, aggregate: str, base_url: str = DEFAULT_BASE_URL) -> str:
    apply_query = f"?$apply=groupby(({groupby}), aggregate({aggregate}))"
    url = f"{base_url.rstrip('/')}/{entity_set}{apply_query}"
    resp = make_odata_request(url, base_url=base_url)
    
    if resp.ok:
        try:
            return truncate_response(resp.json())
        except Exception as e:
            return f"❌ Failed to parse JSON response: {str(e)}"

    # fallback to manual aggregation if $apply is not supported
    fallback_url = f"{base_url.rstrip('/')}/{entity_set}?$format=json"
    resp = make_odata_request(fallback_url, base_url=base_url)
    if not resp.ok:
        return f"❌ Failed to fetch fallback: {resp.status_code} - {resp.text}"
    
    try:
        data = resp.json().get("d", {}).get("results", [])
        if not data:
            return "No data to aggregate."

        # ✅ Parse "GrossAmount with sum as TotalSales"
        match = re.match(r'(\w+)\s+with\s+(\w+)\s+as\s+(\w+)', aggregate.strip(), re.IGNORECASE)
        if not match:
            return f"❌ Invalid aggregate format: {aggregate}"
        
        field, func, alias = match.groups()
        func = func.lower()

        group_map = defaultdict(list)
        for row in data:
            group_key = row.get(groupby)
            value = row.get(field)
            if group_key is not None and isinstance(value, (int, float)):
                group_map[group_key].append(value)

        result = []
        for k, vals in group_map.items():
            try:
                if func == "sum":
                    agg_val = sum(vals)
                elif func == "average":
                    agg_val = sum(vals) / len(vals)
                elif func == "count":
                    agg_val = len(vals)
                else:
                    return f"❌ Unsupported aggregation function: {func}"
                result.append({groupby: k, alias: agg_val})
            except Exception:
                result.append({groupby: k, alias: "❌ Calculation error"})

        return truncate_response({"results": result})
    except Exception as e:
        return f"❌ Manual aggregation failed: {str(e)}"



@mcp.tool(name="odata_get_navigation_paths")
def odata_get_navigation_paths(entity_set: str, base_url: str = DEFAULT_BASE_URL) -> str:
    metadata = get_metadata(base_url)
    tree = ET.fromstring(metadata)
    ns = {"edmx": "http://schemas.microsoft.com/ado/2007/06/edmx",
          "edm": "http://schemas.microsoft.com/ado/2008/09/edm"}

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
