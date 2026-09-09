#!/usr/bin/env python3
"""Demonstrate RDF-native mappings including ordered lists and typed literals."""

from typing import Annotated

from pydantic import Field
from rdflib import Namespace
from rdflib.namespace import XSD

from pydantic_rdf import BaseRdfModel, WithDataType, WithLanguage, WithRdfList
from pydantic_rdf.types import PydanticURIRef

EX = Namespace("https://example.org/")


class Release(BaseRdfModel):
    """A release with RDF-native field mappings."""

    rdf_type = EX.Release
    _rdf_namespace = EX

    name: str = Field(alias="releaseName")
    homepage: PydanticURIRef
    description: Annotated[str, WithLanguage("en")]
    version: Annotated[float, WithDataType(XSD.decimal)]
    ordered_changes: Annotated[list[str], WithRdfList()]
    labels: set[str] = Field(default_factory=set)


def main() -> None:
    """Serialize a release and parse it back."""
    release = Release(
        uri=EX.release_1,
        releaseName="PydanticRDF 1.0",
        homepage=EX.homepage,
        description="An RDF-aware Pydantic release",
        version=1.0,
        ordered_changes=["URI references", "Language-tagged literals", "RDF lists"],
        labels={"pydantic", "rdf"},
    )
    graph = release.model_dump_rdf()
    print(graph.serialize(format="turtle"))

    loaded = Release.parse_graph(graph, EX.release_1)
    print(f"Loaded {loaded.name} from {loaded.homepage}")


if __name__ == "__main__":
    main()
