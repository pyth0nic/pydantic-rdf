from datetime import date, datetime, time
from io import StringIO
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import Field
from rdflib import OWL, RDF, RDFS, SH, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

from pydantic_rdf import BaseRdfModel, WithDataType, WithLanguage, WithPredicate, WithRdfList
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


def test_all_entities_reuses_nested_models(graph: Graph, EX: Namespace):
    class Author(BaseRdfModel):
        rdf_type = EX.Author
        _rdf_namespace = EX

        name: str

    class Book(BaseRdfModel):
        rdf_type = EX.Book
        _rdf_namespace = EX

        author: Author
        title: str

    shared_author = Author(uri=EX.author, name="Ada")
    graph += Book(uri=EX.book_one, title="One", author=shared_author).model_dump_rdf()
    graph += Book(uri=EX.book_two, title="Two", author=shared_author).model_dump_rdf()

    books = Book.all_entities(graph)

    assert len(books) == 2
    assert books[0].author is books[1].author


def test_schema_export_supports_owl_shacl_versioning_and_streams(EX: Namespace):
    class Author(BaseRdfModel):
        rdf_type = EX.Author
        _rdf_namespace = EX

        name: str

    class Book(BaseRdfModel):
        rdf_type = EX.Book
        _rdf_namespace = EX

        title: str = Field(description="The book title")
        author: Author
        pages: int | None = None

    schema = Book.model_dump_schema_rdf(ontology=EX.ontology, version="1.2.0", version_iri=EX.ontology_v1_2_0)
    assert (EX.ontology, RDF.type, OWL.Ontology) in schema
    assert (EX.ontology, OWL.versionInfo, Literal("1.2.0")) in schema
    assert (EX.Book, RDF.type, OWL.Class) in schema
    assert (EX.author, RDF.type, OWL.ObjectProperty) in schema
    assert (EX.author, RDFS.range, EX.Author) in schema
    assert (URIRef(f"{EX.Book}Shape"), SH.targetClass, EX.Book) in schema

    stream = StringIO()
    assert Book.model_dump_schema_rdf(stream, format="turtle") is None
    assert "owl:Ontology" in stream.getvalue()


def test_schema_export_writes_parseable_formats_to_path(tmp_path: Path, EX: Namespace):
    class Event(BaseRdfModel):
        rdf_type = EX.Event
        _rdf_namespace = EX

        happened_on: date
        happened_at: datetime
        happened_time: time
        is_public: bool
        labels: list[str]

    for suffix, format in ((".ttl", "turtle"), (".jsonld", "json-ld"), (".rdf", "xml")):
        destination = tmp_path / f"event-schema{suffix}"
        Event.model_dump_schema_rdf(destination, format=format)
        assert destination.exists()
        parsed = Graph().parse(destination, format=format)
        assert (EX.happened_on, RDFS.range, XSD.date) in parsed
        assert (EX.happened_at, RDFS.range, XSD.dateTime) in parsed
        assert (EX.happened_time, RDFS.range, XSD.time) in parsed
        assert (EX.is_public, RDFS.range, XSD.boolean) in parsed


def test_schema_export_represents_predicates_and_cardinality(EX: Namespace):
    class Tag(BaseRdfModel):
        rdf_type = EX.Tag
        _rdf_namespace = EX

        label: str

    class Article(BaseRdfModel):
        rdf_type = EX.Article
        _rdf_namespace = EX

        title: Annotated[str, WithPredicate(EX.headline)]
        tags: list[Tag] = Field(default_factory=list)
        published: bool | None = None

    schema = Article.model_dump_schema_rdf()
    shape = URIRef(f"{EX.Article}Shape")
    title_shape = next(
        candidate for candidate in schema.objects(shape, SH.property) if (candidate, SH.path, EX.headline) in schema
    )

    assert (EX.headline, RDF.type, OWL.DatatypeProperty) in schema
    assert (title_shape, SH.minCount, Literal(1)) in schema
    assert (title_shape, SH.maxCount, Literal(1)) in schema
    assert (EX.tags, RDF.type, OWL.ObjectProperty) in schema
    assert (EX.tags, RDFS.range, EX.Tag) in schema
    assert (EX.published, RDFS.range, XSD.boolean) in schema
