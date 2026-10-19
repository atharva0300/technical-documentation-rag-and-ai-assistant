::: rst-class
core-header, orm-addin
:::

# Using INSERT Statements {#tutorial_core_insert}

When using Core as well as when using the ORM for bulk operations, a SQL INSERT statement is generated directly using the `_sql.insert`{.interpreted-text role="func"} function - this function generates a new instance of `_sql.Insert`{.interpreted-text role="class"} which represents an INSERT statement in SQL, that adds new data into a table.

::: {.container .orm-header}
**ORM Readers** -

This section details the Core means of generating an individual SQL INSERT statement in order to add new rows to a table. When using the ORM, we normally use another tool that rides on top of this called the `unit of work`{.interpreted-text role="term"}, which will automate the production of many INSERT statements at once. However, understanding how the Core handles data creation and manipulation is very useful even when the ORM is running it for us. Additionally, the ORM supports direct use of INSERT using a feature called `tutorial_orm_bulk`{.interpreted-text role="ref"}.

To skip directly to how to INSERT rows with the ORM using normal unit of work patterns, see `tutorial_inserting_orm`{.interpreted-text role="ref"}.
:::

## The insert() SQL Expression Construct

A simple example of `_sql.Insert`{.interpreted-text role="class"} illustrating the target table and the VALUES clause at once:

``` pycon+sql
>>> from sqlalchemy import insert
>>> stmt = insert(user_table).values(name="spongebob", fullname="Spongebob Squarepants")
```

The above `stmt` variable is an instance of `_sql.Insert`{.interpreted-text role="class"}. Most SQL expressions can be stringified in place as a means to see the general form of what\'s being produced:

``` pycon+sql
>>> print(stmt)
{printsql}INSERT INTO user_account (name, fullname) VALUES (:name, :fullname)
```

The stringified form is created by producing a `_engine.Compiled`{.interpreted-text role="class"} form of the object which includes a database-specific string SQL representation of the statement; we can acquire this object directly using the `_sql.ClauseElement.compile`{.interpreted-text role="meth"} method:

``` pycon+sql
>>> compiled = stmt.compile()
```

Our `_sql.Insert`{.interpreted-text role="class"} construct is an example of a \"parameterized\" construct, illustrated previously at `tutorial_sending_parameters`{.interpreted-text role="ref"}; to view the `name` and `fullname` `bound parameters`{.interpreted-text role="term"}, these are available from the `_engine.Compiled`{.interpreted-text role="class"} construct as well:

``` pycon+sql
>>> compiled.params
{'name': 'spongebob', 'fullname': 'Spongebob Squarepants'}
```

## Executing the Statement

Invoking the statement we can INSERT a row into `user_table`. The INSERT SQL as well as the bundled parameters can be seen in the SQL logging:

``` pycon+sql
>>> with engine.connect() as conn:
...     result = conn.execute(stmt)
...     conn.commit()
{execsql}BEGIN (implicit)
INSERT INTO user_account (name, fullname) VALUES (?, ?)
[...] ('spongebob', 'Spongebob Squarepants')
COMMIT
```

In its simple form above, the INSERT statement does not return any rows, and if only a single row is inserted, it will usually include the ability to return information about column-level default values that were generated during the INSERT of that row, most commonly an integer primary key value. In the above case the first row in a SQLite database will normally return `1` for the first integer primary key value, which we can acquire using the `_engine.CursorResult.inserted_primary_key`{.interpreted-text role="attr"} accessor:

``` pycon+sql
>>> result.inserted_primary_key
(1,)
```

:::: tip
::: title
Tip
:::

`_engine.CursorResult.inserted_primary_key`{.interpreted-text role="attr"} returns a tuple because a primary key may contain multiple columns. This is known as a `composite primary key`{.interpreted-text role="term"}. The `_engine.CursorResult.inserted_primary_key`{.interpreted-text role="attr"} is intended to always contain the complete primary key of the record just inserted, not just a \"cursor.lastrowid\" kind of value, and is also intended to be populated regardless of whether or not \"autoincrement\" were used, hence to express a complete primary key it\'s a tuple.
::::

::: versionchanged
1.4.8 the tuple returned by `_engine.CursorResult.inserted_primary_key`{.interpreted-text role="attr"} is now a named tuple fulfilled by returning it as a `_result.Row`{.interpreted-text role="class"} object.
:::

## INSERT usually generates the \"values\" clause automatically {#tutorial_core_insert_values_clause}

The example above made use of the `_sql.Insert.values`{.interpreted-text role="meth"} method to explicitly create the VALUES clause of the SQL INSERT statement. If we don\'t actually use `_sql.Insert.values`{.interpreted-text role="meth"} and just print out an \"empty\" statement, we get an INSERT for every column in the table:

``` pycon+sql
>>> print(insert(user_table))
{printsql}INSERT INTO user_account (id, name, fullname) VALUES (:id, :name, :fullname)
```

If we take an `_sql.Insert`{.interpreted-text role="class"} construct that has not had `_sql.Insert.values`{.interpreted-text role="meth"} called upon it and execute it rather than print it, the statement will be compiled to a string based on the parameters that we passed to the `_engine.Connection.execute`{.interpreted-text role="meth"} method, and only include columns relevant to the parameters that were passed. This is actually the usual way that `_sql.Insert`{.interpreted-text role="class"} is used to insert rows without having to type out an explicit VALUES clause. The example below illustrates a two-column INSERT statement being executed with a list of parameters at once:

``` pycon+sql
>>> with engine.connect() as conn:
...     result = conn.execute(
...         insert(user_table),
...         [
...             {"name": "sandy", "fullname": "Sandy Cheeks"},
...             {"name": "patrick", "fullname": "Patrick Star"},
...         ],
...     )
...     conn.commit()
{execsql}BEGIN (implicit)
INSERT INTO user_account (name, fullname) VALUES (?, ?)
[...] [('sandy', 'Sandy Cheeks'), ('patrick', 'Patrick Star')]
COMMIT{stop}
```

The execution above features \"executemany\" form first illustrated at `tutorial_multiple_parameters`{.interpreted-text role="ref"}, however unlike when using the `_sql.text`{.interpreted-text role="func"} construct, we didn\'t have to spell out any SQL. By passing a dictionary or list of dictionaries to the `_engine.Connection.execute`{.interpreted-text role="meth"} method in conjunction with the `_sql.Insert`{.interpreted-text role="class"} construct, the `_engine.Connection`{.interpreted-text role="class"} ensures that the column names which are passed will be expressed in the VALUES clause of the `_sql.Insert`{.interpreted-text role="class"} construct automatically.

:::: tip
::: title
Tip
:::

When passing a list of dictionaries to `_engine.Connection.execute`{.interpreted-text role="meth"} along with a Core `_sql.Insert`{.interpreted-text role="class"}, **only the first dictionary in the list determines what columns will be in the VALUES clause**. The rest of the dictionaries are not scanned. This is both because within traditional `executemany()`, the INSERT statement can only have one VALUES clause for all parameters, and additionally SQLAlchemy does not want to add overhead by scanning every parameter dictionary to verify each contains the identical keys as the first one.

Note this behavior is distinctly different from that of an `ORM
enabled INSERT <tutorial_orm_bulk>`{.interpreted-text role="ref"}, introduced later in this tutorial, which performs a full scan of parameter sets in terms of an ORM entity.
::::

::: deepalchemy
Hi, welcome to the first edition of **Deep Alchemy**. The person on the left is known as **The Alchemist**, and you\'ll note they are **not** a wizard, as the pointy hat is not sticking upwards. The Alchemist comes around to describe things that are generally **more advanced and/or tricky** and additionally **not usually needed**, but for whatever reason they feel you should know about this thing that SQLAlchemy can do.

In this edition, towards the goal of having some interesting data in the `address_table` as well, below is a more advanced example illustrating how the `_sql.Insert.values`{.interpreted-text role="meth"} method may be used explicitly while at the same time including for additional VALUES generated from the parameters. A `scalar subquery`{.interpreted-text role="term"} is constructed, making use of the `_sql.select`{.interpreted-text role="func"} construct introduced in the next section, and the parameters used in the subquery are set up using an explicit bound parameter name, established using the `_sql.bindparam`{.interpreted-text role="func"} construct.

This is some slightly **deeper** alchemy just so that we can add related rows without fetching the primary key identifiers from the `user_table` operation into the application. Most Alchemists will simply use the ORM which takes care of things like this for us.

``` pycon+sql
>>> from sqlalchemy import select, bindparam
>>> scalar_subq = (
...     select(user_table.c.id)
...     .where(user_table.c.name == bindparam("username"))
...     .scalar_subquery()
... )

>>> with engine.connect() as conn:
...     result = conn.execute(
...         insert(address_table).values(user_id=scalar_subq),
...         [
...             {
...                 "username": "spongebob",
...                 "email_address": "spongebob@sqlalchemy.org",
...             },
...             {"username": "sandy", "email_address": "sandy@sqlalchemy.org"},
...             {"username": "sandy", "email_address": "sandy@squirrelpower.org"},
...         ],
...     )
...     conn.commit()
{execsql}BEGIN (implicit)
INSERT INTO address (user_id, email_address) VALUES ((SELECT user_account.id
FROM user_account
WHERE user_account.name = ?), ?)
[...] [('spongebob', 'spongebob@sqlalchemy.org'), ('sandy', 'sandy@sqlalchemy.org'),
('sandy', 'sandy@squirrelpower.org')]
COMMIT{stop}
```

With that, we have some more interesting data in our tables that we will make use of in the upcoming sections.
:::

:::: tip
::: title
Tip
:::

A true \"empty\" INSERT that inserts only the \"defaults\" for a table without including any explicit values at all is generated if we indicate `_sql.Insert.values`{.interpreted-text role="meth"} with no arguments; not every database backend supports this, but here\'s what SQLite produces:

``` pycon+sql
>>> print(insert(user_table).values().compile(engine))
```

{printsql}INSERT INTO user_account DEFAULT VALUES
::::

## INSERT\...RETURNING {#tutorial_insert_returning}

The RETURNING clause for supported backends is used automatically in order to retrieve the last inserted primary key value as well as the values for server defaults. However the RETURNING clause may also be specified explicitly using the `_sql.Insert.returning`{.interpreted-text role="meth"} method; in this case, the `_engine.Result`{.interpreted-text role="class"} object that\'s returned when the statement is executed has rows which can be fetched:

``` pycon+sql
>>> insert_stmt = insert(address_table).returning(
...     address_table.c.id, address_table.c.email_address
... )
>>> print(insert_stmt)
{printsql}INSERT INTO address (id, user_id, email_address)
VALUES (:id, :user_id, :email_address)
RETURNING address.id, address.email_address
```

It can also be combined with `_sql.Insert.from_select`{.interpreted-text role="meth"}, as in the example below that builds upon the example stated in `tutorial_insert_from_select`{.interpreted-text role="ref"}:

``` pycon+sql
>>> select_stmt = select(user_table.c.id, user_table.c.name + "@aol.com")
>>> insert_stmt = insert(address_table).from_select(
...     ["user_id", "email_address"], select_stmt
... )
>>> print(insert_stmt.returning(address_table.c.id, address_table.c.email_address))
{printsql}INSERT INTO address (user_id, email_address)
SELECT user_account.id, user_account.name || :name_1 AS anon_1
FROM user_account RETURNING address.id, address.email_address
```

:::: tip
::: title
Tip
:::

The RETURNING feature is also supported by UPDATE and DELETE statements, which will be introduced later in this tutorial.

For INSERT statements, the RETURNING feature may be used both for single-row statements as well as for statements that INSERT multiple rows at once. Support for multiple-row INSERT with RETURNING is dialect specific, however is supported for all the dialects that are included in SQLAlchemy which support RETURNING. See the section `engine_insertmanyvalues`{.interpreted-text role="ref"} for background on this feature.
::::

::: seealso
Bulk INSERT with or without RETURNING is also supported by the ORM. See `orm_queryguide_bulk_insert`{.interpreted-text role="ref"} for reference documentation.
:::

## INSERT\...FROM SELECT {#tutorial_insert_from_select}

A less used feature of `_sql.Insert`{.interpreted-text role="class"}, but here for completeness, the `_sql.Insert`{.interpreted-text role="class"} construct can compose an INSERT that gets rows directly from a SELECT using the `_sql.Insert.from_select`{.interpreted-text role="meth"} method. This method accepts a `_sql.select`{.interpreted-text role="func"} construct, which is discussed in the next section, along with a list of column names to be targeted in the actual INSERT. In the example below, rows are added to the `address` table which are derived from rows in the `user_account` table, giving each user a free email address at `aol.com`:

``` pycon+sql
>>> select_stmt = select(user_table.c.id, user_table.c.name + "@aol.com")
>>> insert_stmt = insert(address_table).from_select(
...     ["user_id", "email_address"], select_stmt
... )
>>> print(insert_stmt)
{printsql}INSERT INTO address (user_id, email_address)
SELECT user_account.id, user_account.name || :name_1 AS anon_1
FROM user_account
```

This construct is used when one wants to copy data from some other part of the database directly into a new set of rows, without actually fetching and re-sending the data from the client.

::: seealso
`_sql.Insert`{.interpreted-text role="class"} - in the SQL Expression API documentation
:::
