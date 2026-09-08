#!/usr/bin/env python3
"""Export an RDF model's OWL ontology and SHACL shape in multiple RDF formats."""

from pathlib import Path
from typing import Annotated

from pydantic import Field
from rdflib import Namespace

from pydantic_rdf import BaseRdfModel, WithPredicate

EX = Namespace("https://example.org/")


class Person(BaseRdfModel):
    """A person schema exported as OWL and SHACL."""

    rdf_type = EX.Person
    _rdf_namespace = EX

    name: str = Field(description="The person's full name")
    email: Annotated[str, WithPredicate(EX.email)]
    knows: list["Person"] = Field(default_factory=list)


def main() -> None:
    """Write a versioned schema as Turtle and JSON-LD."""
    schema = Person.model_dump_schema_rdf(ontology=EX.ontology, version="1.0.0", version_iri=EX.ontology_v1)
    print(schema.serialize(format="turtle"))

    Person.model_dump_schema_rdf(
        Path("person-schema.ttl"),
        ontology=EX.ontology,
        version="1.0.0",
        version_iri=EX.ontology_v1,
    )
    with Path("person-schema.jsonld").open("w", encoding="utf-8") as output:
        Person.model_dump_schema_rdf(
            output,
            format="json-ld",
            ontology=EX.ontology,
            version="1.0.0",
            version_iri=EX.ontology_v1,
        )


if __name__ == "__main__":
    main()
