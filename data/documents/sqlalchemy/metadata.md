# Working with Database Metadata {#tutorial_working_with_metadata}

With engines and SQL execution down, we are ready to begin some Alchemy. The central element of both SQLAlchemy Core and ORM is the SQL Expression Language which allows for fluent, composable construction of SQL queries. The foundation for these queries are Python objects that represent database concepts like tables and columns. These objects are known collectively as `database metadata`{.interpreted-text role="term"}.

The most common foundational objects for database metadata in SQLAlchemy are known as `_schema.MetaData`{.interpreted-text role="class"}, `_schema.Table`{.interpreted-text role="class"}, and `_schema.Column`{.interpreted-text role="class"}. The sections below will illustrate how these objects are used in both a Core-oriented style as well as an ORM-oriented style.

::: {.container .orm-header}
**ORM readers, stay with us!**

As with other sections, Core users can skip the ORM sections, but ORM users would best be familiar with these objects from both perspectives. The `.Table`{.interpreted-text role="class"} object discussed here is declared in a more indirect (and also fully Python-typed) way when using the ORM, however there is still a `.Table`{.interpreted-text role="class"} object within the ORM\'s configuration.
:::

::: rst-class
core-header, orm-dependency
:::

## Setting up MetaData with Table objects {#tutorial_core_metadata}

When we work with a relational database, the basic data-holding structure in the database which we query from is known as a **table**. In SQLAlchemy, the database \"table\" is ultimately represented by a Python object similarly named `_schema.Table`{.interpreted-text role="class"}.

To start using the SQLAlchemy Expression Language, we will want to have `_schema.Table`{.interpreted-text role="class"} objects constructed that represent all of the database tables we are interested in working with. The `_schema.Table`{.interpreted-text role="class"} is constructed programmatically, either directly by using the `_schema.Table`{.interpreted-text role="class"} constructor, or indirectly by using ORM Mapped classes (described later at `tutorial_orm_table_metadata`{.interpreted-text role="ref"}). There is also the option to load some or all table information from an existing database, called `reflection`{.interpreted-text role="term"}.

Whichever kind of approach is used, we always start out with a collection that will be where we place our tables known as the `_schema.MetaData`{.interpreted-text role="class"} object. This object is essentially a `facade`{.interpreted-text role="term"} around a Python dictionary that stores a series of `_schema.Table`{.interpreted-text role="class"} objects keyed to their string name. While the ORM provides some options on where to get this collection, we always have the option to simply make one directly, which looks like:

    >>> from sqlalchemy import MetaData
    >>> metadata_obj = MetaData()

Once we have a `_schema.MetaData`{.interpreted-text role="class"} object, we can declare some `_schema.Table`{.interpreted-text role="class"} objects. This tutorial will start with the classic SQLAlchemy tutorial model, which has a table called `user_account` that stores, for example, the users of a website, and a related table `address`, which stores email addresses associated with rows in the `user_account` table. When not using ORM Declarative models at all, we construct each `_schema.Table`{.interpreted-text role="class"} object directly, typically assigning each to a variable that will be how we will refer to the table in application code:

    >>> from sqlalchemy import Table, Column, Integer, String
    >>> user_table = Table(
    ...     "user_account",
    ...     metadata_obj,
    ...     Column("id", Integer, primary_key=True),
    ...     Column("name", String(30)),
    ...     Column("fullname", String),
    ... )

With the above example, when we wish to write code that refers to the `user_account` table in the database, we will use the `user_table` Python variable to refer to it.

::: topic
**When do I make a `MetaData` object in my program?**

Having a single `_schema.MetaData`{.interpreted-text role="class"} object for an entire application is the most common case, represented as a module-level variable in a single place in an application, often in a \"models\" or \"dbschema\" type of package. It is also very common that the `_schema.MetaData`{.interpreted-text role="class"} is accessed via an ORM-centric `_orm.registry`{.interpreted-text role="class"} or `Declarative Base <tutorial_orm_declarative_base>`{.interpreted-text role="ref"} base class, so that this same `_schema.MetaData`{.interpreted-text role="class"} is shared among ORM- and Core-declared `_schema.Table`{.interpreted-text role="class"} objects.

There can be multiple `_schema.MetaData`{.interpreted-text role="class"} collections as well; `_schema.Table`{.interpreted-text role="class"} objects can refer to `_schema.Table`{.interpreted-text role="class"} objects in other collections without restrictions. However, for groups of `_schema.Table`{.interpreted-text role="class"} objects that are related to each other, it is in practice much more straightforward to have them set up within a single `_schema.MetaData`{.interpreted-text role="class"} collection, both from the perspective of declaring them, as well as from the perspective of DDL (i.e. CREATE and DROP) statements being emitted in the correct order.
:::

### Components of `Table`

We can observe that the `_schema.Table`{.interpreted-text role="class"} construct as written in Python has a resemblance to a SQL CREATE TABLE statement; starting with the table name, then listing out each column, where each column has a name and a datatype. The objects we use above are:

- `_schema.Table`{.interpreted-text role="class"} - represents a database table and assigns itself to a `_schema.MetaData`{.interpreted-text role="class"} collection.

- `_schema.Column`{.interpreted-text role="class"} - represents a column in a database table, and assigns itself to a `_schema.Table`{.interpreted-text role="class"} object. The `_schema.Column`{.interpreted-text role="class"} usually includes a string name and a type object. The collection of `_schema.Column`{.interpreted-text role="class"} objects in terms of the parent `_schema.Table`{.interpreted-text role="class"} are typically accessed via an associative array located at `_schema.Table.c`{.interpreted-text role="attr"}:

      >>> user_table.c.name
      Column('name', String(length=30), table=<user_account>)

      >>> user_table.c.keys()
      ['id', 'name', 'fullname']

- `_types.Integer`{.interpreted-text role="class"}, `_types.String`{.interpreted-text role="class"} - these classes represent SQL datatypes and can be passed to a `_schema.Column`{.interpreted-text role="class"} with or without necessarily being instantiated. Above, we want to give a length of \"30\" to the \"name\" column, so we instantiated `String(30)`. But for \"id\" and \"fullname\" we did not specify these, so we can send the class itself.

::: seealso
The reference and API documentation for `_schema.MetaData`{.interpreted-text role="class"}, `_schema.Table`{.interpreted-text role="class"} and `_schema.Column`{.interpreted-text role="class"} is at `metadata_toplevel`{.interpreted-text role="ref"}. The reference documentation for datatypes is at `types_toplevel`{.interpreted-text role="ref"}.
:::

In an upcoming section, we will illustrate one of the fundamental functions of `_schema.Table`{.interpreted-text role="class"} which is to generate `DDL`{.interpreted-text role="term"} on a particular database connection. But first we will declare a second `_schema.Table`{.interpreted-text role="class"}.

### Declaring Simple Constraints

The first `_schema.Column`{.interpreted-text role="class"} in the example `user_table` includes the `_schema.Column.primary_key`{.interpreted-text role="paramref"} parameter which is a shorthand technique of indicating that this `_schema.Column`{.interpreted-text role="class"} should be part of the primary key for this table. The primary key itself is normally declared implicitly and is represented by the `_schema.PrimaryKeyConstraint`{.interpreted-text role="class"} construct, which we can see on the `_schema.Table.primary_key`{.interpreted-text role="attr"} attribute on the `_schema.Table`{.interpreted-text role="class"} object:

    >>> user_table.primary_key
    PrimaryKeyConstraint(Column('id', Integer(), table=<user_account>, primary_key=True, nullable=False))

The constraint that is most typically declared explicitly is the `_schema.ForeignKeyConstraint`{.interpreted-text role="class"} object that corresponds to a database `foreign key constraint`{.interpreted-text role="term"}. When we declare tables that are related to each other, SQLAlchemy uses the presence of these foreign key constraint declarations not only so that they are emitted within CREATE statements to the database, but also to assist in constructing SQL expressions.

A `_schema.ForeignKeyConstraint`{.interpreted-text role="class"} that involves only a single column on the target table is typically declared using a column-level shorthand notation via the `_schema.ForeignKey`{.interpreted-text role="class"} object. Below we declare a second table `address` that will have a foreign key constraint referring to the `user` table:

    >>> from sqlalchemy import ForeignKey
    >>> address_table = Table(
    ...     "address",
    ...     metadata_obj,
    ...     Column("id", Integer, primary_key=True),
    ...     Column("user_id", ForeignKey("user_account.id"), nullable=False),
    ...     Column("email_address", String, nullable=False),
    ... )

The table above also features a third kind of constraint, which in SQL is the \"NOT NULL\" constraint, indicated above using the `_schema.Column.nullable`{.interpreted-text role="paramref"} parameter.

:::: tip
::: title
Tip
:::

When using the `_schema.ForeignKey`{.interpreted-text role="class"} object within a `_schema.Column`{.interpreted-text role="class"} definition, we can omit the datatype for that `_schema.Column`{.interpreted-text role="class"}; it is automatically inferred from that of the related column, in the above example the `_types.Integer`{.interpreted-text role="class"} datatype of the `user_account.id` column.
::::

### Using `.TypedColumns`{.interpreted-text role="class"} to get a better typing experience

A SQLAlchemy `_schema.Table`{.interpreted-text role="class"} can also be defined using a `_schema.TypedColumns`{.interpreted-text role="class"} to offers better integration with type checker and IDEs. The tables defined above could be declared as follows:

    >>> from sqlalchemy import Named, TypedColumns, Table
    >>> other_meta = MetaData()
    >>> class user_cols(TypedColumns):
    ...     id: Named[int] = Column(primary_key=True)
    ...     name: Named[str | None] = Column(String(30))
    ...     fullname: Named[str | None]

    >>> typed_user_table = Table("user_account", other_meta, user_cols)

    >>> class address_cols(TypedColumns):
    ...     id: Named[int] = Column(primary_key=True)
    ...     user_id: Named[int] = Column(ForeignKey("user_account.id"))
    ...     email_address: Named[str]
    ...     __row_pos__: tuple[int, int, str]

    >>> typed_address_table = Table("address", other_meta, address_cols)

The columns are defined by subclassing `.TypedColumns`{.interpreted-text role="class"}, so that static type checkers can understand what columns are present in the `_schema.Table.c`{.interpreted-text role="attr"} collection. Functionally the two methods of defining the metadata objects are equivalent. The optional `__row_pos__` annotation is an aid to type checker so that they can correctly suggest the type to apply when selecting from the complete table, without specifying the single columns.

In the next section we will emit the completed DDL for the `user_account` and `address` table to see the completed result.

### Emitting DDL to the Database {#tutorial_emitting_ddl}

We\'ve constructed an object structure that represents two database tables in a database, starting at the root `_schema.MetaData`{.interpreted-text role="class"} object, then into two `_schema.Table`{.interpreted-text role="class"} objects, each of which hold onto a collection of `_schema.Column`{.interpreted-text role="class"} and `_schema.Constraint`{.interpreted-text role="class"} objects. This object structure will be at the center of most operations we perform with both Core and ORM going forward.

The first useful thing we can do with this structure will be to emit CREATE TABLE statements, or `DDL`{.interpreted-text role="term"}, to our SQLite database so that we can insert and query data from them. We have already all the tools needed to do so, by invoking the `_schema.MetaData.create_all`{.interpreted-text role="meth"} method on our `_schema.MetaData`{.interpreted-text role="class"}, sending it the `_engine.Engine`{.interpreted-text role="class"} that refers to the target database:

``` pycon+sql
>>> metadata_obj.create_all(engine)
{execsql}BEGIN (implicit)
PRAGMA main.table_...info("user_account")
...
PRAGMA main.table_...info("address")
...
CREATE TABLE user_account (
    id INTEGER NOT NULL,
    name VARCHAR(30),
    fullname VARCHAR,
    PRIMARY KEY (id)
)
...
CREATE TABLE address (
    id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    email_address VARCHAR NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES user_account (id)
)
...
COMMIT
```

The DDL create process above includes some SQLite-specific PRAGMA statements that test for the existence of each table before emitting a CREATE. The full series of steps are also included within a BEGIN/COMMIT pair to accommodate for transactional DDL.

The create process also takes care of emitting CREATE statements in the correct order; above, the FOREIGN KEY constraint is dependent on the `user` table existing, so the `address` table is created second. In more complicated dependency scenarios the FOREIGN KEY constraints may also be applied to tables after the fact using ALTER.

The `_schema.MetaData`{.interpreted-text role="class"} object also features a `_schema.MetaData.drop_all`{.interpreted-text role="meth"} method that will emit DROP statements in the reverse order as it would emit CREATE in order to drop schema elements.

::: topic
**Migration tools are usually appropriate**

Overall, the CREATE / DROP feature of `_schema.MetaData`{.interpreted-text role="class"} is useful for test suites, small and/or new applications, and applications that use short-lived databases. For management of an application database schema over the long term however, a schema management tool such as [Alembic](https://alembic.sqlalchemy.org), which builds upon SQLAlchemy, is likely a better choice, as it can manage and orchestrate the process of incrementally altering a fixed database schema over time as the design of the application changes.
:::

::: rst-class
orm-header
:::

## Using ORM Declarative Forms to Define Table Metadata {#tutorial_orm_table_metadata}

::: topic
**Another way to make Table objects?**

The preceding examples illustrated direct use of the `_schema.Table`{.interpreted-text role="class"} object, which underlies how SQLAlchemy ultimately refers to database tables when constructing SQL expressions. As mentioned, the SQLAlchemy ORM provides for a facade around the `_schema.Table`{.interpreted-text role="class"} declaration process referred towards as **Declarative Table**. The Declarative Table process accomplishes the same goal as we had in the previous section, that of building `_schema.Table`{.interpreted-text role="class"} objects, but also within that process gives us something else called an `ORM mapped class`{.interpreted-text role="term"}, or just \"mapped class\". The mapped class is the most common foundational unit of SQL when using the ORM, and in modern SQLAlchemy can also be used quite effectively with Core-centric use as well.

Some benefits of using Declarative Table include:

- A more succinct and Pythonic style of setting up column definitions, where Python types may be used to represent SQL types to be used in the database
- The resulting mapped class can be used to form SQL expressions that in many cases maintain `484`{.interpreted-text role="pep"} typing information that\'s picked up by static analysis tools such as Mypy and IDE type checkers
- Allows declaration of table metadata and the ORM mapped class used in persistence / object loading operations all at once.

This section will illustrate the same `_schema.Table`{.interpreted-text role="class"} metadata of the previous section(s) being constructed using Declarative Table.
:::

When using the ORM, the process by which we declare `_schema.Table`{.interpreted-text role="class"} metadata is usually combined with the process of declaring `mapped`{.interpreted-text role="term"} classes. The mapped class is any Python class we\'d like to create, which will then have attributes on it that will be linked to the columns in a database table. While there are a few varieties of how this is achieved, the most common style is known as `declarative <orm_declarative_mapper_config_toplevel>`{.interpreted-text role="ref"}, and allows us to declare our user-defined classes and `_schema.Table`{.interpreted-text role="class"} metadata at once.

### Establishing a Declarative Base {#tutorial_orm_declarative_base}

When using the ORM, the `_schema.MetaData`{.interpreted-text role="class"} collection remains present, however it itself is associated with an ORM-only construct commonly referred towards as the **Declarative Base**. The most expedient way to acquire a new Declarative Base is to create a new class that subclasses the SQLAlchemy `_orm.DeclarativeBase`{.interpreted-text role="class"} class:

    >>> from sqlalchemy.orm import DeclarativeBase
    >>> class Base(DeclarativeBase):
    ...     pass

Above, the `Base` class is what we\'ll call the Declarative Base. When we make new classes that are subclasses of `Base`, combined with appropriate class-level directives, they will each be established as a new **ORM mapped class** at class creation time, each one typically (but not exclusively) referring to a particular `_schema.Table`{.interpreted-text role="class"} object.

The Declarative Base refers to a `_schema.MetaData`{.interpreted-text role="class"} collection that is created for us automatically, assuming we didn\'t provide one from the outside. This `.MetaData`{.interpreted-text role="class"} collection is accessible via the `_orm.DeclarativeBase.metadata`{.interpreted-text role="attr"} class-level attribute. As we create new mapped classes, they each will reference a `.Table`{.interpreted-text role="class"} within this `.MetaData`{.interpreted-text role="class"} collection:

    >>> Base.metadata
    MetaData()

The Declarative Base also refers to a collection called `_orm.registry`{.interpreted-text role="class"}, which is the central \"mapper configuration\" unit in the SQLAlchemy ORM. While seldom accessed directly, this object is central to the mapper configuration process, as a set of ORM mapped classes will coordinate with each other via this registry. As was the case with `.MetaData`{.interpreted-text role="class"}, our Declarative Base also created a `_orm.registry`{.interpreted-text role="class"} for us (again with options to pass our own `_orm.registry`{.interpreted-text role="class"}), which we can access via the `_orm.DeclarativeBase.registry`{.interpreted-text role="attr"} class variable:

    >>> Base.registry
    <sqlalchemy.orm.decl_api.registry object at 0x...>

::: topic
**Other ways to map with the `registry`**

`_orm.DeclarativeBase`{.interpreted-text role="class"} is not the only way to map classes, only the most common. `_orm.registry`{.interpreted-text role="class"} also provides other mapper configurational patterns, including decorator-oriented and imperative ways to map classes. There\'s also full support for creating Python dataclasses while mapping. The reference documentation at `mapper_config_toplevel`{.interpreted-text role="ref"} has it all.
:::

### Declaring Mapped Classes {#tutorial_declaring_mapped_classes}

With the `Base` class established, we can now define ORM mapped classes for the `user_account` and `address` tables in terms of new classes `User` and `Address`. We illustrate below the most modern form of Declarative, which is driven from `484`{.interpreted-text role="pep"} type annotations using a special type `.Mapped`{.interpreted-text role="class"}, which indicates attributes to be mapped as particular types:

    >>> from typing import List
    >>> from typing import Optional
    >>> from sqlalchemy.orm import Mapped
    >>> from sqlalchemy.orm import mapped_column
    >>> from sqlalchemy.orm import relationship

    >>> class User(Base):
    ...     __tablename__ = "user_account"
    ...
    ...     id: Mapped[int] = mapped_column(primary_key=True)
    ...     name: Mapped[str] = mapped_column(String(30))
    ...     fullname: Mapped[Optional[str]]
    ...
    ...     addresses: Mapped[List["Address"]] = relationship(back_populates="user")
    ...
    ...     def __repr__(self) -> str:
    ...         return f"User(id={self.id!r}, name={self.name!r}, fullname={self.fullname!r})"

    >>> class Address(Base):
    ...     __tablename__ = "address"
    ...
    ...     id: Mapped[int] = mapped_column(primary_key=True)
    ...     email_address: Mapped[str]
    ...     user_id = mapped_column(ForeignKey("user_account.id"))
    ...
    ...     user: Mapped[User] = relationship(back_populates="addresses")
    ...
    ...     def __repr__(self) -> str:
    ...         return f"Address(id={self.id!r}, email_address={self.email_address!r})"

The two classes above, `User` and `Address`, are now called as **ORM Mapped Classes**, and are available for use in ORM persistence and query operations, which will be described later. Details about these classes include:

- Each class refers to a `_schema.Table`{.interpreted-text role="class"} object that was generated as part of the declarative mapping process, which is named by assigning a string to the `_orm.DeclarativeBase.__tablename__`{.interpreted-text role="attr"} attribute. Once the class is created, this generated `_schema.Table`{.interpreted-text role="class"} is available from the `_orm.DeclarativeBase.__table__`{.interpreted-text role="attr"} attribute.

- As mentioned previously, this form is known as `orm_declarative_table_configuration`{.interpreted-text role="ref"}. One of several alternative declaration styles would instead have us build the `_schema.Table`{.interpreted-text role="class"} object directly, and **assign** it directly to `_orm.DeclarativeBase.__table__`{.interpreted-text role="attr"}. This style is known as `Declarative with Imperative Table <orm_imperative_table_configuration>`{.interpreted-text role="ref"}.

- To indicate columns in the `_schema.Table`{.interpreted-text role="class"}, we use the `_orm.mapped_column`{.interpreted-text role="func"} construct, in combination with typing annotations based on the `_orm.Mapped`{.interpreted-text role="class"} type. This object will generate `_schema.Column`{.interpreted-text role="class"} objects that are applied to the construction of the `_schema.Table`{.interpreted-text role="class"}.

- For columns with simple datatypes and no other options, we can indicate a `_orm.Mapped`{.interpreted-text role="class"} type annotation alone, using simple Python types like `int` and `str` to mean `.Integer`{.interpreted-text role="class"} and `.String`{.interpreted-text role="class"}. Customization of how Python types are interpreted within the Declarative mapping process is very open ended; see the sections `orm_declarative_mapped_column`{.interpreted-text role="ref"} and `orm_declarative_mapped_column_type_map`{.interpreted-text role="ref"} for background.

- A column can be declared as \"nullable\" or \"not null\" based on the presence of the `Optional[<typ>]` type annotation (or its equivalents, `<typ> | None` or `Union[<typ>, None]`). The `_orm.mapped_column.nullable`{.interpreted-text role="paramref"} parameter may also be used explicitly (and does not have to match the annotation\'s optionality).

- Use of explicit typing annotations is **completely optional**. We can also use `_orm.mapped_column`{.interpreted-text role="func"} without annotations. When using this form, we would use more explicit type objects like `.Integer`{.interpreted-text role="class"} and `.String`{.interpreted-text role="class"} as well as `nullable=False` as needed within each `_orm.mapped_column`{.interpreted-text role="func"} construct.

- Two additional attributes, `User.addresses` and `Address.user`, define a different kind of attribute called `_orm.relationship`{.interpreted-text role="func"}, which features similar annotation-aware configuration styles as shown. The `_orm.relationship`{.interpreted-text role="func"} construct is discussed more fully at `tutorial_orm_related_objects`{.interpreted-text role="ref"}.

- The classes are automatically given an `__init__()` method if we don\'t declare one of our own. The default form of this method accepts all attribute names as optional keyword arguments:

      >>> sandy = User(name="sandy", fullname="Sandy Cheeks")

  To automatically generate a full-featured `__init__()` method which provides for positional arguments as well as arguments with default keyword values, the dataclasses feature introduced at `orm_declarative_native_dataclasses`{.interpreted-text role="ref"} may be used. It\'s of course always an option to use an explicit `__init__()` method as well.

- The `__repr__()` methods are added so that we get a readable string output; there\'s no requirement for these methods to be here. As is the case with `__init__()`, a `__repr__()` method can be generated automatically by using the `dataclasses <orm_declarative_native_dataclasses>`{.interpreted-text role="ref"} feature.

::: topic
**Where\'d the old Declarative go?**

Users of SQLAlchemy 1.4 or previous will note that the above mapping uses a dramatically different form than before; not only does it use `_orm.mapped_column`{.interpreted-text role="func"} instead of `.Column`{.interpreted-text role="class"} in the Declarative mapping, it also uses Python type annotations to derive column information.

To provide context for users of the \"old\" way, Declarative mappings can still be made using `.Column`{.interpreted-text role="class"} objects (as well as using the `_orm.declarative_base`{.interpreted-text role="func"} function to create the base class) as before, and these forms will continue to be supported with no plans to remove support. The reason these two facilities are superseded by new constructs is first and foremost to integrate smoothly with `484`{.interpreted-text role="pep"} tools, including IDEs such as VSCode and type checkers such as Mypy and Pyright, without the need for plugins. Secondly, deriving the declarations from type annotations is part of SQLAlchemy\'s integration with Python dataclasses, which can now be `generated natively <orm_declarative_native_dataclasses>`{.interpreted-text role="ref"} from mappings.

For users who like the \"old\" way, but still desire their IDEs to not mistakenly report typing errors for their declarative mappings, the `_orm.mapped_column`{.interpreted-text role="func"} construct is a drop-in replacement for `.Column`{.interpreted-text role="class"} in an ORM Declarative mapping (note that `_orm.mapped_column`{.interpreted-text role="func"} is for ORM Declarative mappings only; it can\'t be used within a `.Table`{.interpreted-text role="class"} construct), and the type annotations are optional. Our mapping above can be written without annotations as:

    class User(Base):
        __tablename__ = "user_account"

        id = mapped_column(Integer, primary_key=True)
        name = mapped_column(String(30), nullable=False)
        fullname = mapped_column(String)

        addresses = relationship("Address", back_populates="user")

        # ... definition continues

The above class has an advantage over one that uses `.Column`{.interpreted-text role="class"} directly, in that the `User` class as well as instances of `User` will indicate the correct typing information to typing tools, without the use of plugins. `_orm.mapped_column`{.interpreted-text role="func"} also allows for additional ORM-specific parameters to configure behaviors such as deferred column loading, which previously needed a separate `_orm.deferred`{.interpreted-text role="func"} function to be used with `_schema.Column`{.interpreted-text role="class"}.

There\'s also an example of converting an old-style Declarative class to the new style, which can be seen at `whatsnew_20_orm_declarative_typing`{.interpreted-text role="ref"} in the `whatsnew_20_toplevel`{.interpreted-text role="ref"} guide.
:::

::: seealso
`orm_mapping_styles`{.interpreted-text role="ref"} - full background on different ORM configurational styles.

`orm_declarative_mapping`{.interpreted-text role="ref"} - overview of Declarative class mapping

`orm_declarative_table`{.interpreted-text role="ref"} - detail on how to use `_orm.mapped_column`{.interpreted-text role="func"} and `_orm.Mapped`{.interpreted-text role="class"} to define the columns within a `_schema.Table`{.interpreted-text role="class"} to be mapped when using Declarative.
:::

### Emitting DDL to the database from an ORM mapping

As our ORM mapped classes refer to `_schema.Table`{.interpreted-text role="class"} objects contained within a `_schema.MetaData`{.interpreted-text role="class"} collection, emitting DDL given the Declarative Base uses the same process as that described previously at `tutorial_emitting_ddl`{.interpreted-text role="ref"}. In our case, we have already generated the `user` and `address` tables in our SQLite database. If we had not done so already, we would be free to make use of the `_schema.MetaData`{.interpreted-text role="class"} associated with our ORM Declarative Base class in order to do so, by accessing the collection from the `_orm.DeclarativeBase.metadata`{.interpreted-text role="attr"} attribute and then using `_schema.MetaData.create_all`{.interpreted-text role="meth"} as before. In this case, PRAGMA statements are run, but no new tables are generated since they are found to be present already:

``` pycon+sql
>>> Base.metadata.create_all(engine)
{execsql}BEGIN (implicit)
PRAGMA main.table_...info("user_account")
...
PRAGMA main.table_...info("address")
...
COMMIT
```

::: rst-class
core-header, orm-addin
:::

## Table Reflection {#tutorial_table_reflection}

::: topic
**Optional Section**

This section is just a brief introduction to the related subject of **table reflection**, or how to generate `_schema.Table`{.interpreted-text role="class"} objects automatically from an existing database. Tutorial readers who want to get on with writing queries can feel free to skip this section.
:::

To round out the section on working with table metadata, we will illustrate another operation that was mentioned at the beginning of the section, that of **table reflection**. Table reflection refers to the process of generating `_schema.Table`{.interpreted-text role="class"} and related objects by reading the current state of a database. Whereas in the previous sections we\'ve been declaring `_schema.Table`{.interpreted-text role="class"} objects in Python, where we then have the option to emit DDL to the database to generate such a schema, the reflection process does these two steps in reverse, starting from an existing database and generating in-Python data structures to represent the schemas within that database.

:::: tip
::: title
Tip
:::

There is no requirement that reflection must be used in order to use SQLAlchemy with a pre-existing database. It is entirely typical that the SQLAlchemy application declares all metadata explicitly in Python, such that its structure corresponds to the existing database. The metadata structure also need not include tables, columns, or other constraints and constructs in the pre-existing database that are not needed for the local application to function.
::::

As an example of reflection, we will create a new `_schema.Table`{.interpreted-text role="class"} object which represents the `some_table` object we created manually in the earlier sections of this document. There are again some varieties of how this is performed, however the most basic is to construct a `_schema.Table`{.interpreted-text role="class"} object, given the name of the table and a `_schema.MetaData`{.interpreted-text role="class"} collection to which it will belong, then instead of indicating individual `_schema.Column`{.interpreted-text role="class"} and `_schema.Constraint`{.interpreted-text role="class"} objects, pass it the target `_engine.Engine`{.interpreted-text role="class"} using the `_schema.Table.autoload_with`{.interpreted-text role="paramref"} parameter:

``` pycon+sql
>>> some_table = Table("some_table", metadata_obj, autoload_with=engine)
{execsql}BEGIN (implicit)
PRAGMA main.table_...info("some_table")
[raw sql] ()
SELECT sql FROM  (SELECT * FROM sqlite_master UNION ALL   SELECT * FROM sqlite_temp_master) WHERE name = ? AND type in ('table', 'view')
[raw sql] ('some_table',)
PRAGMA main.foreign_key_list("some_table")
...
PRAGMA main.index_list("some_table")
...
ROLLBACK{stop}
```

At the end of the process, the `some_table` object now contains the information about the `_schema.Column`{.interpreted-text role="class"} objects present in the table, and the object is usable in exactly the same way as a `_schema.Table`{.interpreted-text role="class"} that we declared explicitly:

    >>> some_table
    Table('some_table', MetaData(),
        Column('x', INTEGER(), table=<some_table>),
        Column('y', INTEGER(), table=<some_table>),
        schema=None)

::: seealso
Read more about table and schema reflection at `metadata_reflection_toplevel`{.interpreted-text role="ref"}.

For ORM-related variants of table reflection, the section `orm_declarative_reflected`{.interpreted-text role="ref"} includes an overview of the available options.
:::

## Next Steps

We now have a SQLite database ready to go with two tables present, and Core and ORM table-oriented constructs that we can use to interact with these tables via a `_engine.Connection`{.interpreted-text role="class"} and/or ORM `_orm.Session`{.interpreted-text role="class"}. In the following sections, we will illustrate how to create, manipulate, and select data using these structures.
