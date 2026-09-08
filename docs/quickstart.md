# Quick Start Guide

This guide will get you started with PydanticRDF quickly, showing basic usage patterns.

## Define Your First Model

To use PydanticRDF, you create classes that inherit from `BaseRdfModel` and define RDF mapping details:

```python
from rdflib import SDO
from pydantic_rdf import BaseRdfModel, WithPredicate
from pydantic import Annotated

# Define a model using Schema.org types
class Person(BaseRdfModel):
    # RDF type for this model (maps to rdf:type)
    rdf_type = SDO.Person
    
    # Default namespace for properties
    _rdf_namespace = SDO
    
    # Model fields
    name: str
    email: str
    job_title: Annotated[str, WithPredicate(SDO.jobTitle)] # Custom predicate
```

## Create and Serialize Instances

Once you have defined your model, you can create instances and serialize them to RDF:

```python
# Create an instance
person = Person(
    uri=SDO.Person_1,  # URI is a required field for all RDF models
    name="John Doe",
    email="john.doe@example.com",
    job_title="Software Engineer"
)

# Serialize to RDF graph
graph = person.model_dump_rdf()

# Print the graph as Turtle format
print(graph.serialize(format="turtle"))
```

The output will be an RDF graph with triples representing the model:

```
@prefix schema: <https://schema.org/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

schema:Person/1 a schema:Person ;
    schema:email "john.doe@example.com" ;
    schema:jobTitle "Software Engineer" ;
    schema:name "John Doe" .
```

## Deserialize from RDF

You can also deserialize RDF data back into model instances:

```python
# Parse an instance from the graph
loaded_person = Person.parse_graph(graph, SDO.Person_1)

# Access attributes
assert loaded_person.name == "John Doe"
assert loaded_person.email == "john.doe@example.com"
assert loaded_person.job_title == "Software Engineer"
```

## Working with Nested Models

PydanticRDF supports nested models and relationships:

```python
class PostalAddress(BaseRdfModel):
    rdf_type = SDO.PostalAddress
    _rdf_namespace = SDO
    
    streetAddress: str
    addressLocality: str

class PersonWithAddress(BaseRdfModel):
    rdf_type = SDO.Person
    _rdf_namespace = SDO
    
    name: str
    address: PostalAddress

# Create nested models
address = PostalAddress(uri=SDO.PostalAddress_1, streetAddress="123 Main St", addressLocality="Springfield")
person = PersonWithAddress(uri=SDO.Person_2, name="John Doe", address=address)

# Serialize to RDF
graph = person.model_dump_rdf()
```

## Working with Lists

PydanticRDF supports lists of items:

```python
class BlogPosting(BaseRdfModel):
    rdf_type = SDO.BlogPosting
    _rdf_namespace = SDO
    
    headline: str
    keywords: list[str]  # Will create multiple triples with the same predicate

# Create with a list
post = BlogPosting(
    uri=SDO.BlogPosting_1,
    headline="PydanticRDF Introduction",
    keywords=["RDF", "Pydantic", "Python"]
)

# Serialize to RDF
graph = post.model_dump_rdf()
```

Lists use repeated predicate triples by default because RDF graphs are unordered. Use
`WithRdfList()` when collection order is meaningful; it stores the values as an RDF
collection and restores their order when parsing.

```python
from typing import Annotated
from pydantic_rdf import WithRdfList

tracks: Annotated[list[str], WithRdfList()]
```

## RDF Term and Literal Mapping

`PydanticURIRef` fields are emitted as RDF resource references, not string literals.
Use `WithDataType` to select a literal datatype and `WithLanguage` for language-tagged
strings.

```python
from typing import Annotated
from rdflib.namespace import XSD
from pydantic_rdf import WithDataType, WithLanguage

title: Annotated[str, WithLanguage("en")]
price: Annotated[float, WithDataType(XSD.decimal)]
```

Blank nodes are intentionally unsupported as model identifiers or nested resources and
raise `UnsupportedRdfTermError`; use stable URI references for RDF entities. Dictionaries
are stored as JSON literals. Native RDFLib literals preserve standard temporal datatypes.

## Pydantic Integration

Pydantic field aliases are used as default RDF predicate local names, so
`display_name: str = Field(alias="displayName")` maps to `namespace:displayName`.
`model_dump_rdf()` accepts the same keyword serialization options as `model_dump()`,
such as `exclude_none=True`. PEP 604 optional fields, annotated fields, and list, tuple,
set, and frozenset collections are supported.

## Efficient Bulk Loading

Use `Model.all_entities(graph)` to load all resources of a type. It shares one parse cache
for the batch, so nested resources referenced by several models are parsed once and retain
their shared identity. This avoids repeated graph traversals for common relationship graphs.

## More Examples

- [`basic_usage.py`](https://github.com/Omegaice/pydantic-rdf/blob/master/examples/basic_usage.py):
  Schema.org people, addresses, and repeated predicates.
- [`sparql_integration.py`](https://github.com/Omegaice/pydantic-rdf/blob/master/examples/sparql_integration.py):
  query RDF with SPARQL before converting results to models.
- [`rdf_features.py`](https://github.com/Omegaice/pydantic-rdf/blob/master/examples/rdf_features.py):
  aliases, URI references, language-tagged and datatype literals, RDF lists, and sets.

## Migration from Earlier Releases

PydanticRDF now requires Pydantic 2.12 or newer. Upgrade the lock file with
`uv lock --upgrade-package pydantic`, then synchronize with `uv sync --all-groups`.
Existing list fields keep their repeated-triple mapping; add `WithRdfList()` only where
ordering needs to be preserved. URI fields previously emitted as literals are now RDF
resource references.

## JSON Schema Generation

PydanticRDF supports generating valid JSON schemas for your RDF models:

```python
from pydantic import TypeAdapter

# Define your model as before
class Person(BaseRdfModel):
    rdf_type = SDO.Person
    _rdf_namespace = SDO
    
    name: str
    email: str

# Generate JSON schema
schema = TypeAdapter(Person).json_schema()

# URIRef fields will be properly represented as strings with URI format
# {
#   "properties": {
#     "uri": {
#       "type": "string",
#       "format": "uri",
#       "description": "The URI identifier for this RDF entity"
#     },
#     "name": {
#       "type": "string"
#     },
#     "email": {
#       "type": "string"
#     }
#   },
#   "required": ["uri", "name", "email"],
#   ...
# }
```

## Next Steps

Now that you have the basics, you can:

- Explore the [API reference](reference/pydantic_rdf/index.md) for detailed documentation
