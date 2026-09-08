from typing import Annotated

import pytest
from pydantic import Field
from rdflib import RDF, XSD, BNode, Graph, Literal, Namespace
from rdflib.collection import Collection

from pydantic_rdf import BaseRdfModel, WithDataType, WithLanguage, WithRdfList
from pydantic_rdf.exceptions import CircularReferenceError, UnsupportedRdfTermError
from pydantic_rdf.types import PydanticURIRef


def test_pep604_collections_aliases_and_urirefs_round_trip(graph: Graph, EX: Namespace):
    class Resource(BaseRdfModel):
        rdf_type = EX.Resource
        _rdf_namespace = EX

        label: str = Field(alias="displayName")
        related: PydanticURIRef | None = None
        tags: list[str] | None = None
        codes: set[int] = Field(default_factory=set)

    resource = Resource(displayName="Example", uri=EX.resource, related=EX.related, tags=["one", "two"], codes={1, 2})
    graph += resource.model_dump_rdf()

    assert (EX.resource, EX.displayName, Literal("Example")) in graph
    assert (EX.resource, EX.related, EX.related) in graph
    parsed = Resource.parse_graph(graph, EX.resource)
    assert parsed.label == resource.label
    assert parsed.related == resource.related
    assert set(parsed.tags or []) == set(resource.tags or [])
    assert parsed.codes == resource.codes


def test_rdf_list_datatype_and_language_round_trip(graph: Graph, EX: Namespace):
    class Playlist(BaseRdfModel):
        rdf_type = EX.Playlist
        _rdf_namespace = EX

        title: Annotated[str, WithLanguage("en")]
        price: Annotated[float, WithDataType(XSD.decimal)]
        tracks: Annotated[list[str], WithRdfList()]

    playlist = Playlist(uri=EX.playlist, title="Music", price=12.5, tracks=["first", "second"])
    graph += playlist.model_dump_rdf()

    list_head = graph.value(EX.playlist, EX.tracks)
    assert list(Collection(graph, list_head)) == [Literal("first"), Literal("second")]
    assert (EX.playlist, EX.title, Literal("Music", lang="en")) in graph
    assert (EX.playlist, EX.price, Literal(12.5, datatype=XSD.decimal)) in graph
    assert Playlist.parse_graph(graph, EX.playlist) == playlist


def test_blank_nodes_are_rejected_explicitly(graph: Graph, EX: Namespace):
    class Resource(BaseRdfModel):
        rdf_type = EX.Resource
        _rdf_namespace = EX

        name: str

    node = BNode()
    graph.add((node, RDF.type, EX.Resource))

    with pytest.raises(UnsupportedRdfTermError):
        Resource.parse_graph(graph, node)  # type: ignore[arg-type]


def test_cyclic_serialization_is_rejected(EX: Namespace):
    class Node(BaseRdfModel):
        rdf_type = EX.Node
        _rdf_namespace = EX

        name: str
        next: "Node | None" = None

    first = Node(uri=EX.first, name="first")
    second = Node(uri=EX.second, name="second", next=first)
    first.next = second

    with pytest.raises(CircularReferenceError):
        first.model_dump_rdf()
