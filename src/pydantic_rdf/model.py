import json
import logging
from collections.abc import MutableMapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from types import UnionType
from typing import (
    IO,
    Annotated,
    Any,
    ClassVar,
    Final,
    Self,
    TypeAlias,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
)

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo
from rdflib import OWL, RDF, RDFS, SH, XSD, BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.term import Node

from pydantic_rdf.annotation import WithDataType, WithLanguage, WithPredicate, WithRdfList
from pydantic_rdf.exceptions import CircularReferenceError, UnsupportedFieldTypeError, UnsupportedRdfTermError
from pydantic_rdf.types import IsDefinedNamespace, IsPrefixNamespace, PydanticURIRef, TypeInfo

logger = logging.getLogger(__name__)


T = TypeVar("T", bound="BaseRdfModel")
M = TypeVar("M", bound="BaseRdfModel")  # For cls parameter annotations

# Sentinel object to detect circular references during parsing
_IN_PROGRESS: Final = object()


CacheKey: TypeAlias = tuple[type["BaseRdfModel"], URIRef]
RDFEntityCache: TypeAlias = MutableMapping[CacheKey, object]
RdfDestination: TypeAlias = str | Path | IO[str]


class BaseRdfModel(BaseModel):
    """Base class for RDF-mappable Pydantic models."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Class variables for RDF mapping
    rdf_type: ClassVar[PydanticURIRef]
    _rdf_namespace: ClassVar[IsPrefixNamespace | IsDefinedNamespace]

    uri: PydanticURIRef = Field(description="The URI identifier for this RDF entity")

    # TYPE ANALYSIS HELPERS
    @classmethod
    def _get_field_predicate(cls: type[M], field_name: str, field: FieldInfo) -> URIRef:
        """Return the RDF predicate URI for a given field name and FieldInfo.

        Returns:
            The RDF predicate URIRef for the field.
        """
        if predicate := WithPredicate.extract(field):
            return predicate
        return cls._rdf_namespace[field.serialization_alias or field.alias or field_name]

    @staticmethod
    def _get_annotated_type(annotation: Any) -> Any | None:
        """Return the type wrapped by Annotated, or None if not Annotated.

        Args:
            annotation: The type annotation to inspect.

        Returns:
            The type wrapped by Annotated, or None if not Annotated.
        """
        if get_origin(annotation) is Annotated:
            return get_args(annotation)[0]
        return None

    @staticmethod
    def _get_union_type(annotation: Any) -> Any | None:
        """Return the first non-None type in a Union/Optional annotation, or None.

        Args:
            annotation: The type annotation to inspect.

        Returns:
            The first non-None type in the Union, or None if not a Union.
        """
        if get_origin(annotation) in (Union, UnionType):
            args = get_args(annotation)
            if type(None) in args:
                # Return the first non-None type (for Optional)
                return next((arg for arg in args if arg is not type(None)), None)
        return None

    @staticmethod
    def _get_sequence_type(annotation: Any) -> Any | None:
        """Return the item type if annotation is a Sequence (not str), else None.

        Args:
            annotation: The type annotation to inspect.

        Returns:
            The item type if annotation is a Sequence (not str), else None.
        """
        origin = get_origin(annotation)
        if origin in (list, tuple, set, frozenset) or (
            isinstance(origin, type) and issubclass(origin, Sequence) and not issubclass(origin, str)
        ):
            return get_args(annotation)[0]
        return None

    @classmethod
    def _get_item_type(cls, annotation: Any) -> Any:
        """Recursively unwraps annotation to return the underlying item type.

        Args:
            annotation: The type annotation to unwrap.

        Returns:
            The underlying item type after unwrapping Annotated, Sequence, and Union.
        """
        for extractor in (cls._get_annotated_type, cls._get_sequence_type, cls._get_union_type):
            if (item_type := extractor(annotation)) is not None:
                return cls._get_item_type(item_type)
        return annotation

    @classmethod
    def _resolve_type_info(cls, annotation: Any) -> TypeInfo:
        """Return TypeInfo indicating if annotation is a list and its item type.

        Args:
            annotation: The type annotation to analyze.

        Returns:
            TypeInfo indicating whether the annotation is a list and its item type.
        """
        if (item_type := cls._get_annotated_type(annotation)) is not None:
            return cls._resolve_type_info(item_type)
        if (item_type := cls._get_union_type(annotation)) is not None:
            return cls._resolve_type_info(item_type)
        if (item_type := cls._get_sequence_type(annotation)) is not None:
            return TypeInfo(is_list=True, item_type=cls._get_item_type(item_type))
        return TypeInfo(is_list=False, item_type=annotation)

    # FIELD EXTRACTION AND CONVERSION
    @classmethod
    def _extract_model_type(cls, type_annotation: Any) -> type["BaseRdfModel"] | None:
        """Return the BaseRdfModel subclass from a type annotation, or None.

        Args:
            type_annotation: The type annotation to inspect.

        Returns:
            The BaseRdfModel subclass if present, else None.
        """
        # Self reference
        if type_annotation is Self:
            return cls

        if (item_type := cls._get_annotated_type(type_annotation)) is not None:
            return cls._extract_model_type(item_type)

        if (item_type := cls._get_sequence_type(type_annotation)) is not None:
            return cls._extract_model_type(item_type)

        # Direct BaseRdfModel type
        if get_origin(type_annotation) is None:
            if (
                isinstance(type_annotation, type)
                and issubclass(type_annotation, BaseRdfModel)
                and type_annotation is not BaseRdfModel
            ):
                return type_annotation
            return None

        # Union/Optional types
        if (item_type := cls._get_union_type(type_annotation)) is not None:
            return cls._extract_model_type(item_type)

        return None

    @classmethod
    def _convert_rdf_value(
        cls: type[M],
        graph: Graph,
        value: Any,
        type_annotation: Any,
        cache: RDFEntityCache,
    ) -> Any:
        """Convert an RDF value to a Python value or nested BaseRdfModel instance.

        Returns:
            The converted Python value or BaseRdfModel instance.

        Raises:
            CircularReferenceError: If a circular reference is detected during parsing.
        """
        # Check if this is a nested BaseRdfModel
        if isinstance(value, BNode):
            raise UnsupportedRdfTermError(value)

        if (model_type := cls._extract_model_type(type_annotation)) and isinstance(value, URIRef):
            # Handle nested BaseRdfModel instances with caching to prevent recursion
            if cached := cache.get((model_type, value)):
                # Check for circular references
                if cached is _IN_PROGRESS:
                    raise CircularReferenceError(value)
                return cached
            return model_type.parse_graph(graph, value, _cache=cache)

        # Convert literals to Python values
        if isinstance(value, Literal):
            python_value = value.toPython()
            # Handle JSON strings for dictionary fields
            origin = get_origin(cls._get_item_type(type_annotation))
            if origin is dict and isinstance(python_value, str):
                try:
                    return json.loads(python_value)
                except json.JSONDecodeError:
                    pass  # If not valid JSON, return as is
            return python_value

        return value

    @classmethod
    def _values_from_rdf_list(cls, graph: Graph, value: object) -> list[object]:
        """Return the members of an RDF list value."""
        if not isinstance(value, BNode | URIRef):
            raise UnsupportedRdfTermError(value)
        return list(Collection(graph, value))

    @classmethod
    def _extract_field_value(
        cls: type[M],
        graph: Graph,
        uri: URIRef,
        field_name: str,
        field: FieldInfo,
        cache: RDFEntityCache,
    ) -> Any | None:
        """Extract and convert the value(s) for a field from the RDF graph.

        Returns:
            The extracted and converted value(s) for the field, or None if not present.

        Raises:
            UnsupportedFieldTypeError: If the field type is not supported for RDF parsing.
        """
        # Get all values for this predicate
        predicate = cls._get_field_predicate(field_name, field)
        values: list[object] = list(graph.objects(uri, predicate))
        if not values:
            return None

        # Check if this is a list type
        type_info = cls._resolve_type_info(field.annotation)

        # Check for unsupported types
        if type_info.item_type is complex:
            raise UnsupportedFieldTypeError(type_info.item_type, field_name)

        # Process the values based on their type
        if type_info.is_list:
            if WithRdfList.extract(field):
                annotation = field.annotation
                while True:
                    if (inner := cls._get_annotated_type(annotation)) is not None:
                        annotation = inner
                        continue
                    if (inner := cls._get_union_type(annotation)) is not None:
                        annotation = inner
                        continue
                    break
                if get_origin(annotation) in (set, frozenset):
                    raise ValueError(f"WithRdfList requires an ordered collection (list/tuple): {field_name}")
                if len(values) != 1:
                    raise ValueError(f"Expected one RDF list for field {field_name}")
                values = cls._values_from_rdf_list(graph, values[0])
            return [cls._convert_rdf_value(graph, v, type_info.item_type, cache) for v in values]

        return cls._convert_rdf_value(graph, values[0], type_info.item_type, cache)

    # RDF PARSING
    @classmethod
    def parse_graph(cls: type[T], graph: Graph, uri: URIRef, _cache: RDFEntityCache | None = None) -> T:
        """Parse an RDF entity from the graph into a model instance.

        Uses a cache to prevent recursion and circular references.

        Args:
            _cache: Optional cache for already-parsed entities.

        Returns:
            An instance of the model corresponding to the RDF entity.

        Raises:
            CircularReferenceError: If a circular reference is detected during parsing.
            ValueError: If the URI does not have the expected RDF type.
            UnsupportedFieldTypeError: If a field type is not supported for RDF parsing.

        Example:
            ```python
            model = MyModel.parse_graph(graph, EX.some_uri)
            ```
        """
        if isinstance(uri, BNode):
            raise UnsupportedRdfTermError(uri)

        # Initialize cache if not provided
        cache: RDFEntityCache = {} if _cache is None else _cache

        # Return from cache if already constructed
        if cached := cache.get((cls, uri)):
            if cached is _IN_PROGRESS:
                raise CircularReferenceError(uri)
            return cast(T, cached)

        # Mark entry in cache as being built
        cache[(cls, uri)] = _IN_PROGRESS

        # Verify the entity has the correct RDF type
        if (uri, RDF.type, cls.rdf_type) not in graph:
            raise ValueError(f"URI {uri} does not have type {cls.rdf_type}")

        # Collect field data from the graph
        data: dict[str, Any] = {}
        for field_name, field in cls.model_fields.items():
            if field_name in BaseRdfModel.model_fields:
                continue
            value = cls._extract_field_value(graph, uri, field_name, field, cache)
            if value is not None:
                validation_name = field.validation_alias or field.alias or field_name
                data[str(validation_name)] = value

        # Construct the instance with validation
        instance = cls.model_validate({"uri": uri, **data})

        # Update cache with the constructed instance
        cache[(cls, uri)] = instance

        return instance

    @classmethod
    def all_entities(cls: type[T], graph: Graph) -> list[T]:
        """Return all entities of this model's RDF type from the graph.

        Reuses one parse cache for the complete result set, avoiding redundant parsing
        when entities share nested RDF resources.

        Returns:
            A list of model instances for each entity of this RDF type in the graph.

        Raises:
            CircularReferenceError: If a circular reference is detected during parsing.
            ValueError: If any entity URI does not have the expected RDF type.
            UnsupportedFieldTypeError: If a field type is not supported for RDF parsing.

        Example:
            ```python
            entities = MyModel.all_entities(graph)
            ```
        """
        cache: RDFEntityCache = {}
        return [
            cls.parse_graph(graph, uri, _cache=cache)
            for uri in graph.subjects(RDF.type, cls.rdf_type)
            if isinstance(uri, URIRef)
        ]

    @classmethod
    def _schema_range(cls, annotation: Any) -> tuple[URIRef, bool]:
        """Return the RDF range and whether it represents a resource."""
        item_type = cls._get_item_type(annotation)
        if model_type := cls._extract_model_type(item_type):
            return model_type.rdf_type, True
        if item_type is URIRef:
            return RDFS.Resource, True
        datatype_ranges: dict[type[object], URIRef] = {
            str: XSD.string,
            bool: XSD.boolean,
            int: XSD.integer,
            float: XSD.double,
            date: XSD.date,
            datetime: XSD.dateTime,
            time: XSD.time,
        }
        return datatype_ranges.get(item_type, RDFS.Literal), False

    @classmethod
    def model_dump_schema_rdf(
        cls: type[T],
        destination: RdfDestination | None = None,
        *,
        format: str = "turtle",
        ontology: URIRef | None = None,
        version: str | None = None,
        version_iri: URIRef | None = None,
    ) -> Graph | str | None:
        """Generate an OWL ontology and SHACL shape graph for the model.

        Args:
            destination: A filename, path, or writable text stream for serialized RDF.
            format: Any RDFLib serializer format, such as ``"turtle"``, ``"json-ld"``,
                ``"xml"``, or ``"n3"``.
            ontology: URI identifying the generated ontology.
            version: Optional ontology version string.
            version_iri: Optional URI identifying this ontology version.

        Returns:
            The schema graph when no destination is provided, otherwise RDFLib's
            serialized string result.
        """
        graph = Graph()
        graph.bind("owl", OWL)
        graph.bind("rdfs", RDFS)
        graph.bind("sh", SH)
        graph.bind("xsd", XSD)

        ontology = ontology or URIRef(str(cls._rdf_namespace))
        shape = URIRef(f"{cls.rdf_type}Shape")
        graph.add((ontology, RDF.type, OWL.Ontology))
        graph.add((cls.rdf_type, RDF.type, OWL.Class))
        graph.add((shape, RDF.type, SH.NodeShape))
        graph.add((shape, SH.targetClass, cls.rdf_type))
        if version is not None:
            graph.add((ontology, OWL.versionInfo, Literal(version)))
        if version_iri is not None:
            graph.add((ontology, OWL.versionIRI, version_iri))

        for field_name, field in cls.model_fields.items():
            if field_name == "uri":
                continue
            predicate = cls._get_field_predicate(field_name, field)
            rdf_range, is_resource = cls._schema_range(field.annotation)
            type_info = cls._resolve_type_info(field.annotation)
            graph.add((predicate, RDF.type, OWL.ObjectProperty if is_resource else OWL.DatatypeProperty))
            graph.add((predicate, RDFS.domain, cls.rdf_type))
            graph.add((predicate, RDFS.range, rdf_range))
            if field.description:
                graph.add((predicate, RDFS.comment, Literal(field.description)))

            property_shape = BNode()
            graph.add((shape, SH.property, property_shape))
            graph.add((property_shape, SH.path, predicate))
            graph.add((property_shape, SH["class"] if is_resource else SH.datatype, rdf_range))
            if field.is_required():
                graph.add((property_shape, SH.minCount, Literal(1)))
            if not type_info.is_list:
                graph.add((property_shape, SH.maxCount, Literal(1)))

        if destination is None:
            return graph
        if hasattr(destination, "write"):
            destination.write(graph.serialize(format=format))
            return None
        return graph.serialize(destination=destination, format=format)

    # SERIALIZATION
    def _rdf_object(self, value: Any, field: FieldInfo) -> Node:
        """Convert one model value to its RDF object term."""
        type_info = type(self)._resolve_type_info(field.annotation)
        item_type = type(self)._get_item_type(type_info.item_type)
        if item_type is URIRef:
            if isinstance(value, BNode):
                raise UnsupportedRdfTermError(value)
            return value if isinstance(value, URIRef) else URIRef(str(value))
        if isinstance(value, BNode):
            raise UnsupportedRdfTermError(value)
        if (item_type is dict or get_origin(item_type) is dict) and isinstance(value, dict):
            value = json.dumps(value, default=str)
        language = WithLanguage.extract(field)
        datatype = WithDataType.extract(field)
        return Literal(value, lang=language, datatype=datatype)

    def _serialize_to_graph(
        self: Self,
        graph: Graph,
        active: set[URIRef],
        serialized: set[URIRef],
        model_dump_kwargs: dict[str, Any],
    ) -> None:
        """Add this model and its reachable models to graph."""
        if self.uri in active:
            raise CircularReferenceError(self.uri)
        if self.uri in serialized:
            return
        active.add(self.uri)
        graph.add((self.uri, RDF.type, self.rdf_type))
        nested_fields = {
            field_name
            for field_name, field in type(self).model_fields.items()
            if type(self)._extract_model_type(field.annotation) is not None
        }
        dump_options = dict(model_dump_kwargs)
        exclude = dump_options.pop("exclude", None)
        if exclude is None:
            exclude = set(nested_fields)
        elif isinstance(exclude, set):
            exclude = set(exclude) | nested_fields
        elif isinstance(exclude, dict):
            exclude = {**exclude, **{name: True for name in nested_fields}}
        else:
            raise TypeError(f"Unsupported exclude type: {type(exclude).__name__}")
        dumped = self.model_dump(exclude=exclude, **dump_options)

        for field_name, field in type(self).model_fields.items():
            if field_name == "uri":
                continue
            type_info = type(self)._resolve_type_info(field.annotation)
            model_type = type(self)._extract_model_type(field.annotation)
            value = getattr(self, field_name) if model_type is not None else dumped.get(field_name)
            if value is None:
                continue
            predicate = self._get_field_predicate(field_name, field)
            values = value if type_info.is_list else [value]

            if WithRdfList.extract(field):
                if not type_info.is_list:
                    raise ValueError(f"WithRdfList requires a collection field: {field_name}")
                list_node = BNode()
                terms: list[Node] = []
                for item in values:
                    if isinstance(item, BaseRdfModel):
                        terms.append(item.uri)
                        item._serialize_to_graph(graph, active, serialized, model_dump_kwargs)
                    else:
                        terms.append(self._rdf_object(item, field))
                Collection(graph, list_node, terms)
                graph.add((self.uri, predicate, list_node))
                continue

            for item in values:
                if isinstance(item, BaseRdfModel):
                    graph.add((self.uri, predicate, item.uri))
                    item._serialize_to_graph(graph, active, serialized, model_dump_kwargs)
                else:
                    graph.add((self.uri, predicate, self._rdf_object(item, field)))

        active.remove(self.uri)
        serialized.add(self.uri)

    def model_dump_rdf(self: Self, **model_dump_kwargs: Any) -> Graph:
        """Serialize this model instance to an RDF graph.

        Returns:
            An RDFLib Graph representing this model instance.

        Example:
            ```python
            graph = instance.model_dump_rdf()
            ```
        """
        graph = Graph()
        self._serialize_to_graph(graph, set(), set(), model_dump_kwargs)
        return graph
