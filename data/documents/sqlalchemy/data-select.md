:::: {#tutorial_selecting_data}
::: rst-class
core-header, orm-dependency
:::
::::

# Using SELECT Statements

For both Core and ORM, the `_sql.select`{.interpreted-text role="func"} function generates a `_sql.Select`{.interpreted-text role="class"} construct which is used for all SELECT queries. Passed to methods like `_engine.Connection.execute`{.interpreted-text role="meth"} in Core and `_orm.Session.execute`{.interpreted-text role="meth"} in ORM, a SELECT statement is emitted in the current transaction and the result rows available via the returned `_engine.Result`{.interpreted-text role="class"} object.

::: {.container .orm-header}
**ORM Readers** - the content here applies equally well to both Core and ORM use and basic ORM variant use cases are mentioned here. However there are a lot more ORM-specific features available as well; these are documented at `queryguide_toplevel`{.interpreted-text role="ref"}.
:::

## The select() SQL Expression Construct

The `_sql.select`{.interpreted-text role="func"} construct builds up a statement in the same way as that of `_sql.insert`{.interpreted-text role="func"}, using a `generative`{.interpreted-text role="term"} approach where each method builds more state onto the object. Like the other SQL constructs, it can be stringified in place:

``` pycon+sql
>>> from sqlalchemy import select
>>> stmt = select(user_table).where(user_table.c.name == "spongebob")
>>> print(stmt)
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = :name_1
```

Also in the same manner as all other statement-level SQL constructs, to actually run the statement we pass it to an execution method. Since a SELECT statement returns rows we can always iterate the result object to get `_engine.Row`{.interpreted-text role="class"} objects back:

``` pycon+sql
>>> with engine.connect() as conn:
...     for row in conn.execute(stmt):
...         print(row)
{execsql}BEGIN (implicit)
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
[...] ('spongebob',){stop}
(1, 'spongebob', 'Spongebob Squarepants')
{execsql}ROLLBACK{stop}
```

When using the ORM, particularly with a `_sql.select`{.interpreted-text role="func"} construct that\'s composed against ORM entities, we will want to execute it using the `_orm.Session.execute`{.interpreted-text role="meth"} method on the `_orm.Session`{.interpreted-text role="class"}; using this approach, we continue to get `_engine.Row`{.interpreted-text role="class"} objects from the result, however these rows are now capable of including complete entities, such as instances of the `User` class, as individual elements within each row:

``` pycon+sql
>>> stmt = select(User).where(User.name == "spongebob")
>>> with Session(engine) as session:
...     for row in session.execute(stmt):
...         print(row)
{execsql}BEGIN (implicit)
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
[...] ('spongebob',){stop}
(User(id=1, name='spongebob', fullname='Spongebob Squarepants'),)
{execsql}ROLLBACK{stop}
```

::: topic
**select() from a Table vs. ORM class**

While the SQL generated in these examples looks the same whether we invoke `select(user_table)` or `select(User)`, in the more general case they do not necessarily render the same thing, as an ORM-mapped class may be mapped to other kinds of \"selectables\" besides tables. The `select()` that\'s against an ORM entity also indicates that ORM-mapped instances should be returned in a result, which is not the case when SELECTing from a `_schema.Table`{.interpreted-text role="class"} object.
:::

The following sections will discuss the SELECT construct in more detail.

## Setting the COLUMNS and FROM clause {#tutorial_selecting_columns}

The `_sql.select`{.interpreted-text role="func"} function accepts positional elements representing any number of `_schema.Column`{.interpreted-text role="class"} and/or `_schema.Table`{.interpreted-text role="class"} expressions, as well as a wide range of compatible objects, which are resolved into a list of SQL expressions to be SELECTed from that will be returned as columns in the result set. These elements also serve in simpler cases to create the FROM clause, which is inferred from the columns and table-like expressions passed:

``` pycon+sql
>>> print(select(user_table))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
```

To SELECT from individual columns using a Core approach, `_schema.Column`{.interpreted-text role="class"} objects are accessed from the `_schema.Table.c`{.interpreted-text role="attr"} accessor and can be sent directly; the FROM clause will be inferred as the set of all `_schema.Table`{.interpreted-text role="class"} and other `_sql.FromClause`{.interpreted-text role="class"} objects that are represented by those columns:

``` pycon+sql
>>> print(select(user_table.c.name, user_table.c.fullname))
{printsql}SELECT user_account.name, user_account.fullname
FROM user_account
```

Alternatively, when using the `.FromClause.c`{.interpreted-text role="attr"} collection of any `.FromClause`{.interpreted-text role="class"} such as `.Table`{.interpreted-text role="class"}, multiple columns may be specified for a `_sql.select`{.interpreted-text role="func"} by using a tuple of string names:

``` pycon+sql
>>> print(select(user_table.c["name", "fullname"]))
{printsql}SELECT user_account.name, user_account.fullname
FROM user_account
```

::: versionadded
2.0 Added tuple-accessor capability to the `.FromClause.c`{.interpreted-text role="attr"} collection
:::

### Selecting ORM Entities and Columns {#tutorial_selecting_orm_entities}

ORM entities, such our `User` class as well as the column-mapped attributes upon it such as `User.name`, also participate in the SQL Expression Language system representing tables and columns. Below illustrates an example of SELECTing from the `User` entity, which ultimately renders in the same way as if we had used `user_table` directly:

``` pycon+sql
>>> print(select(User))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
```

When executing a statement like the above using the ORM `_orm.Session.execute`{.interpreted-text role="meth"} method, there is an important difference when we select from a full entity such as `User`, as opposed to `user_table`, which is that the **entity itself is returned as a single element within each row**. That is, when we fetch rows from the above statement, as there is only the `User` entity in the list of things to fetch, we get back `_engine.Row`{.interpreted-text role="class"} objects that have only one element, which contain instances of the `User` class:

``` pycon+sql
>>> row = session.execute(select(User)).first()
{execsql}BEGIN...
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
[...] (){stop}
>>> row
(User(id=1, name='spongebob', fullname='Spongebob Squarepants'),)
```

The above `_engine.Row`{.interpreted-text role="class"} has just one element, representing the `User` entity:

``` pycon+sql
>>> row[0]
User(id=1, name='spongebob', fullname='Spongebob Squarepants')
```

A highly recommended convenience method of achieving the same result as above is to use the `_orm.Session.scalars`{.interpreted-text role="meth"} method to execute the statement directly; this method will return a `_result.ScalarResult`{.interpreted-text role="class"} object that delivers the first \"column\" of each row at once, in this case, instances of the `User` class:

``` pycon+sql
>>> user = session.scalars(select(User)).first()
{execsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
[...] (){stop}
>>> user
User(id=1, name='spongebob', fullname='Spongebob Squarepants')
```

Alternatively, we can select individual columns of an ORM entity as distinct elements within result rows, by using the class-bound attributes; when these are passed to a construct such as `_sql.select`{.interpreted-text role="func"}, they are resolved into the `_schema.Column`{.interpreted-text role="class"} or other SQL expression represented by each attribute:

``` pycon+sql
>>> print(select(User.name, User.fullname))
{printsql}SELECT user_account.name, user_account.fullname
FROM user_account
```

When we invoke *this* statement using `_orm.Session.execute`{.interpreted-text role="meth"}, we now receive rows that have individual elements per value, each corresponding to a separate column or other SQL expression:

``` pycon+sql
>>> row = session.execute(select(User.name, User.fullname)).first()
{execsql}SELECT user_account.name, user_account.fullname
FROM user_account
[...] (){stop}
>>> row
('spongebob', 'Spongebob Squarepants')
```

The approaches can also be mixed, as below where we SELECT the `name` attribute of the `User` entity as the first element of the row, and combine it with full `Address` entities in the second element:

``` pycon+sql
>>> session.execute(
...     select(User.name, Address).where(User.id == Address.user_id).order_by(Address.id)
... ).all()
{execsql}SELECT user_account.name, address.id, address.email_address, address.user_id
FROM user_account, address
WHERE user_account.id = address.user_id ORDER BY address.id
[...] (){stop}
[('spongebob', Address(id=1, email_address='spongebob@sqlalchemy.org')),
('sandy', Address(id=2, email_address='sandy@sqlalchemy.org')),
('sandy', Address(id=3, email_address='sandy@squirrelpower.org'))]
```

Approaches towards selecting ORM entities and columns as well as common methods for converting rows are discussed further at `orm_queryguide_select_columns`{.interpreted-text role="ref"}.

::: seealso
`orm_queryguide_select_columns`{.interpreted-text role="ref"} - in the `queryguide_toplevel`{.interpreted-text role="ref"}
:::

### Selecting from Labeled SQL Expressions

The `_sql.ColumnElement.label`{.interpreted-text role="meth"} method as well as the same-named method available on ORM attributes provides a SQL label of a column or expression, allowing it to have a specific name in a result set. This can be helpful when referring to arbitrary SQL expressions in a result row by name:

``` pycon+sql
>>> from sqlalchemy import func, cast
>>> stmt = select(
...     ("Username: " + user_table.c.name).label("username"),
... ).order_by(user_table.c.name)
>>> with engine.connect() as conn:
...     for row in conn.execute(stmt):
...         print(f"{row.username}")
{execsql}BEGIN (implicit)
SELECT ? || user_account.name AS username
FROM user_account ORDER BY user_account.name
[...] ('Username: ',){stop}
Username: patrick
Username: sandy
Username: spongebob
{execsql}ROLLBACK{stop}
```

::: seealso
`tutorial_order_by_label`{.interpreted-text role="ref"} - the label names we create may also be referenced in the ORDER BY or GROUP BY clause of the `_sql.Select`{.interpreted-text role="class"}.
:::

### Selecting with Textual Column Expressions {#tutorial_select_arbitrary_text}

When we construct a `_sql.Select`{.interpreted-text role="class"} object using the `_sql.select`{.interpreted-text role="func"} function, we are normally passing to it a series of `_schema.Table`{.interpreted-text role="class"} and `_schema.Column`{.interpreted-text role="class"} objects that were defined using `table metadata <tutorial_working_with_metadata>`{.interpreted-text role="ref"}, or when using the ORM we may be sending ORM-mapped attributes that represent table columns. However, sometimes there is also the need to manufacture arbitrary SQL blocks inside of statements, such as constant string expressions, or just some arbitrary SQL that\'s quicker to write literally.

The `_sql.text`{.interpreted-text role="func"} construct introduced at `tutorial_working_with_transactions`{.interpreted-text role="ref"} can in fact be embedded into a `_sql.Select`{.interpreted-text role="class"} construct directly, such as below where we manufacture a hardcoded string literal `'some phrase'` and embed it within the SELECT statement:

``` pycon+sql
>>> from sqlalchemy import text
>>> stmt = select(text("'some phrase'"), user_table.c.name).order_by(user_table.c.name)
>>> with engine.connect() as conn:
...     print(conn.execute(stmt).all())
{execsql}BEGIN (implicit)
SELECT 'some phrase', user_account.name
FROM user_account ORDER BY user_account.name
[generated in ...] ()
{stop}[('some phrase', 'patrick'), ('some phrase', 'sandy'), ('some phrase', 'spongebob')]
{execsql}ROLLBACK{stop}
```

While the `_sql.text`{.interpreted-text role="func"} construct can be used in most places to inject literal SQL phrases, more often than not we are actually dealing with textual units that each represent an individual column expression. In this common case we can get more functionality out of our textual fragment using the `_sql.literal_column`{.interpreted-text role="func"} construct instead. This object is similar to `_sql.text`{.interpreted-text role="func"} except that instead of representing arbitrary SQL of any form, it explicitly represents a single \"column\" and can then be labeled and referred towards in subqueries and other expressions:

``` pycon+sql
>>> from sqlalchemy import literal_column
>>> stmt = select(literal_column("'some phrase'").label("p"), user_table.c.name).order_by(
...     user_table.c.name
... )
>>> with engine.connect() as conn:
...     for row in conn.execute(stmt):
...         print(f"{row.p}, {row.name}")
{execsql}BEGIN (implicit)
SELECT 'some phrase' AS p, user_account.name
FROM user_account ORDER BY user_account.name
[generated in ...] ()
{stop}some phrase, patrick
some phrase, sandy
some phrase, spongebob
{execsql}ROLLBACK{stop}
```

Note that in both cases, when using `_sql.text`{.interpreted-text role="func"} or `_sql.literal_column`{.interpreted-text role="func"}, we are writing a syntactical SQL expression, and not a literal value. We therefore have to include whatever quoting or syntaxes are necessary for the SQL we want to see rendered.

## The WHERE clause {#tutorial_select_where_clause}

SQLAlchemy allows us to compose SQL expressions, such as `name = 'squidward'` or `user_id > 10`, by making use of standard Python operators in conjunction with `_schema.Column`{.interpreted-text role="class"} and similar objects. For boolean expressions, most Python operators such as `==`, `!=`, `<`, `>=` etc. generate new SQL Expression objects, rather than plain boolean `True`/`False` values:

``` pycon+sql
>>> print(user_table.c.name == "squidward")
user_account.name = :name_1

>>> print(address_table.c.user_id > 10)
address.user_id > :user_id_1
```

We can use expressions like these to generate the WHERE clause by passing the resulting objects to the `_sql.Select.where`{.interpreted-text role="meth"} method:

``` pycon+sql
>>> print(select(user_table).where(user_table.c.name == "squidward"))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = :name_1
```

To produce multiple expressions joined by AND, the `_sql.Select.where`{.interpreted-text role="meth"} method may be invoked any number of times:

``` pycon+sql
>>> print(
...     select(address_table.c.email_address)
...     .where(user_table.c.name == "squidward")
...     .where(address_table.c.user_id == user_table.c.id)
... )
{printsql}SELECT address.email_address
FROM address, user_account
WHERE user_account.name = :name_1 AND address.user_id = user_account.id
```

A single call to `_sql.Select.where`{.interpreted-text role="meth"} also accepts multiple expressions with the same effect:

``` pycon+sql
>>> print(
...     select(address_table.c.email_address).where(
...         user_table.c.name == "squidward",
...         address_table.c.user_id == user_table.c.id,
...     )
... )
{printsql}SELECT address.email_address
FROM address, user_account
WHERE user_account.name = :name_1 AND address.user_id = user_account.id
```

\"AND\" and \"OR\" conjunctions are both available directly using the `_sql.and_`{.interpreted-text role="func"} and `_sql.or_`{.interpreted-text role="func"} functions, illustrated below in terms of ORM entities:

``` pycon+sql
>>> from sqlalchemy import and_, or_
>>> print(
...     select(Address.email_address).where(
...         and_(
...             or_(User.name == "squidward", User.name == "sandy"),
...             Address.user_id == User.id,
...         )
...     )
... )
{printsql}SELECT address.email_address
FROM address, user_account
WHERE (user_account.name = :name_1 OR user_account.name = :name_2)
AND address.user_id = user_account.id
```

:::: tip
::: title
Tip
:::

The rendering of parentheses is based on operator precedence rules (there\'s no way to detect parentheses from a Python expression at runtime), so if we combine AND and OR in a way that matches the natural precedence of AND, the rendered expression might not have similar looking parentheses as our Python code:

``` pycon+sql
>>> print(
...     select(Address.email_address).where(
...         or_(
...             User.name == "squidward",
...             and_(Address.user_id == User.id, User.name == "sandy"),
...         )
...     )
... )
{printsql}SELECT address.email_address
FROM address, user_account
WHERE user_account.name = :name_1 OR address.user_id = user_account.id AND user_account.name = :name_2
```

More background on parenthesization is in the `operators_parentheses`{.interpreted-text role="ref"} in the Operator Reference.
::::

For simple \"equality\" comparisons against a single entity, there\'s also a popular method known as `_sql.Select.filter_by`{.interpreted-text role="meth"} which accepts keyword arguments that match to column keys or ORM attribute names. It searches across all entities in the FROM clause for the given attribute names:

``` pycon+sql
>>> print(select(User).filter_by(name="spongebob", fullname="Spongebob Squarepants"))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = :name_1 AND user_account.fullname = :fullname_1
```

::: seealso
`/core/operators`{.interpreted-text role="doc"} - descriptions of most SQL operator functions in SQLAlchemy
:::

## Explicit FROM clauses and JOINs {#tutorial_select_join}

As mentioned previously, the FROM clause is usually **inferred** based on the expressions that we are setting in the columns clause as well as other elements of the `_sql.Select`{.interpreted-text role="class"}.

If we set a single column from a particular `_schema.Table`{.interpreted-text role="class"} in the COLUMNS clause, it puts that `_schema.Table`{.interpreted-text role="class"} in the FROM clause as well:

``` pycon+sql
>>> print(select(user_table.c.name))
{printsql}SELECT user_account.name
FROM user_account
```

If we were to put columns from two tables, then we get a comma-separated FROM clause:

``` pycon+sql
>>> print(select(user_table.c.name, address_table.c.email_address))
{printsql}SELECT user_account.name, address.email_address
FROM user_account, address
```

In order to JOIN these two tables together, we typically use one of two methods on `_sql.Select`{.interpreted-text role="class"}. The first is the `_sql.Select.join_from`{.interpreted-text role="meth"} method, which allows us to indicate the left and right side of the JOIN explicitly:

``` pycon+sql
>>> print(
...     select(user_table.c.name, address_table.c.email_address).join_from(
...         user_table, address_table
...     )
... )
{printsql}SELECT user_account.name, address.email_address
FROM user_account JOIN address ON user_account.id = address.user_id
```

The other is the `_sql.Select.join`{.interpreted-text role="meth"} method, which indicates only the right side of the JOIN, the left hand-side is inferred:

``` pycon+sql
>>> print(select(user_table.c.name, address_table.c.email_address).join(address_table))
{printsql}SELECT user_account.name, address.email_address
FROM user_account JOIN address ON user_account.id = address.user_id
```

::: sidebar
**The ON Clause is inferred**

When using `_sql.Select.join_from`{.interpreted-text role="meth"} or `_sql.Select.join`{.interpreted-text role="meth"}, we may observe that the ON clause of the join is also inferred for us in simple foreign key cases. More on that in the next section.
:::

We also have the option to add elements to the FROM clause explicitly, if it is not inferred the way we want from the columns clause. We use the `_sql.Select.select_from`{.interpreted-text role="meth"} method to achieve this, as below where we establish `user_table` as the first element in the FROM clause and `_sql.Select.join`{.interpreted-text role="meth"} to establish `address_table` as the second:

``` pycon+sql
>>> print(select(address_table.c.email_address).select_from(user_table).join(address_table))
{printsql}SELECT address.email_address
FROM user_account JOIN address ON user_account.id = address.user_id
```

Another example where we might want to use `_sql.Select.select_from`{.interpreted-text role="meth"} is if our columns clause doesn\'t have enough information to provide for a FROM clause. For example, to SELECT from the common SQL expression `count(*)`, we use a SQLAlchemy element known as `_sql.func`{.interpreted-text role="attr"} to produce the SQL `count()` function:

``` pycon+sql
>>> from sqlalchemy import func
>>> print(select(func.count("*")).select_from(user_table))
{printsql}SELECT count(:count_2) AS count_1
FROM user_account
```

::: seealso
`orm_queryguide_select_from`{.interpreted-text role="ref"} - in the `queryguide_toplevel`{.interpreted-text role="ref"} - contains additional examples and notes regarding the interaction of `_sql.Select.select_from`{.interpreted-text role="meth"} and `_sql.Select.join`{.interpreted-text role="meth"}.
:::

### Setting the ON Clause {#tutorial_select_join_onclause}

The previous examples of JOIN illustrated that the `_sql.Select`{.interpreted-text role="class"} construct can join between two tables and produce the ON clause automatically. This occurs in those examples because the `user_table` and `address_table` `_sql.Table`{.interpreted-text role="class"} objects include a single `_schema.ForeignKeyConstraint`{.interpreted-text role="class"} definition which is used to form this ON clause.

If the left and right targets of the join do not have such a constraint, or there are multiple constraints in place, we need to specify the ON clause directly. Both `_sql.Select.join`{.interpreted-text role="meth"} and `_sql.Select.join_from`{.interpreted-text role="meth"} accept an additional argument for the ON clause, which is stated using the same SQL Expression mechanics as we saw about in `tutorial_select_where_clause`{.interpreted-text role="ref"}:

``` pycon+sql
>>> print(
...     select(address_table.c.email_address)
...     .select_from(user_table)
...     .join(address_table, user_table.c.id == address_table.c.user_id)
... )
{printsql}SELECT address.email_address
FROM user_account JOIN address ON user_account.id = address.user_id
```

::: {.container .orm-header}
**ORM Tip** - there\'s another way to generate the ON clause when using ORM entities that make use of the `_orm.relationship`{.interpreted-text role="func"} construct, like the mapping set up in the previous section at `tutorial_declaring_mapped_classes`{.interpreted-text role="ref"}. This is a whole subject onto itself, which is introduced at length at `tutorial_joining_relationships`{.interpreted-text role="ref"}.
:::

### OUTER and FULL join

Both the `_sql.Select.join`{.interpreted-text role="meth"} and `_sql.Select.join_from`{.interpreted-text role="meth"} methods accept keyword arguments `_sql.Select.join.isouter`{.interpreted-text role="paramref"} and `_sql.Select.join.full`{.interpreted-text role="paramref"} which will render LEFT OUTER JOIN and FULL OUTER JOIN, respectively:

``` pycon+sql
>>> print(select(user_table).join(address_table, isouter=True))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account LEFT OUTER JOIN address ON user_account.id = address.user_id{stop}

>>> print(select(user_table).join(address_table, full=True))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account FULL OUTER JOIN address ON user_account.id = address.user_id{stop}
```

There is also a method `_sql.Select.outerjoin`{.interpreted-text role="meth"} that is equivalent to using `.join(..., isouter=True)`.

:::: tip
::: title
Tip
:::

SQL also has a \"RIGHT OUTER JOIN\". SQLAlchemy doesn\'t render this directly; instead, reverse the order of the tables and use \"LEFT OUTER JOIN\".
::::

## ORDER BY, GROUP BY, HAVING {#tutorial_order_by_group_by_having}

The SELECT SQL statement includes a clause called ORDER BY which is used to return the selected rows within a given ordering.

The GROUP BY clause is constructed similarly to the ORDER BY clause, and has the purpose of sub-dividing the selected rows into specific groups upon which aggregate functions may be invoked. The HAVING clause is usually used with GROUP BY and is of a similar form to the WHERE clause, except that it\'s applied to the aggregated functions used within groups.

### ORDER BY {#tutorial_order_by}

The ORDER BY clause is constructed in terms of SQL Expression constructs typically based on `_schema.Column`{.interpreted-text role="class"} or similar objects. The `_sql.Select.order_by`{.interpreted-text role="meth"} method accepts one or more of these expressions positionally:

``` pycon+sql
>>> print(select(user_table).order_by(user_table.c.name))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account ORDER BY user_account.name
```

Ascending / descending is available from the `_sql.ColumnElement.asc`{.interpreted-text role="meth"} and `_sql.ColumnElement.desc`{.interpreted-text role="meth"} modifiers, which are present from ORM-bound attributes as well:

``` pycon+sql
>>> print(select(User).order_by(User.fullname.desc()))
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account ORDER BY user_account.fullname DESC
```

The above statement will yield rows that are sorted by the `user_account.fullname` column in descending order.

### Aggregate functions with GROUP BY / HAVING {#tutorial_group_by_w_aggregates}

In SQL, aggregate functions allow column expressions across multiple rows to be aggregated together to produce a single result. Examples include counting, computing averages, as well as locating the maximum or minimum value in a set of values.

SQLAlchemy provides for SQL functions in an open-ended way using a namespace known as `_sql.func`{.interpreted-text role="data"}. This is a special constructor object which will create new instances of `_functions.Function`{.interpreted-text role="class"} when given the name of a particular SQL function, which can have any name, as well as zero or more arguments to pass to the function, which are, like in all other cases, SQL Expression constructs. For example, to render the SQL COUNT() function against the `user_account.id` column, we call upon the `count()` name:

``` pycon+sql
>>> from sqlalchemy import func
>>> count_fn = func.count(user_table.c.id)
>>> print(count_fn)
{printsql}count(user_account.id)
```

SQL functions are described in more detail later in this tutorial at `tutorial_functions`{.interpreted-text role="ref"}.

When using aggregate functions in SQL, the GROUP BY clause is essential in that it allows rows to be partitioned into groups where aggregate functions will be applied to each group individually. When requesting non-aggregated columns in the COLUMNS clause of a SELECT statement, SQL requires that these columns all be subject to a GROUP BY clause, either directly or indirectly based on a primary key association. The HAVING clause is then used in a similar manner as the WHERE clause, except that it filters out rows based on aggregated values rather than direct row contents.

SQLAlchemy provides for these two clauses using the `_sql.Select.group_by`{.interpreted-text role="meth"} and `_sql.Select.having`{.interpreted-text role="meth"} methods. Below we illustrate selecting user name fields as well as count of addresses, for those users that have more than one address:

``` pycon+sql
>>> with engine.connect() as conn:
...     result = conn.execute(
...         select(User.name, func.count(Address.id).label("count"))
...         .join(Address)
...         .group_by(User.name)
...         .having(func.count(Address.id) > 1)
...     )
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT user_account.name, count(address.id) AS count
FROM user_account JOIN address ON user_account.id = address.user_id GROUP BY user_account.name
HAVING count(address.id) > ?
[...] (1,){stop}
[('sandy', 2)]
{execsql}ROLLBACK{stop}
```

### Ordering or Grouping by a Label {#tutorial_order_by_label}

An important technique, in particular on some database backends, is the ability to ORDER BY or GROUP BY an expression that is already stated in the columns clause, without re-stating the expression in the ORDER BY or GROUP BY clause and instead using the column name or labeled name from the COLUMNS clause. This form is available by passing the string text of the name to the `_sql.Select.order_by`{.interpreted-text role="meth"} or `_sql.Select.group_by`{.interpreted-text role="meth"} method. The text passed is **not rendered directly**; instead, the name given to an expression in the columns clause and rendered as that expression name in context, raising an error if no match is found. The unary modifiers `.asc`{.interpreted-text role="func"} and `.desc`{.interpreted-text role="func"} may also be used in this form:

``` pycon+sql
>>> from sqlalchemy import func, desc
>>> stmt = (
...     select(Address.user_id, func.count(Address.id).label("num_addresses"))
...     .group_by("user_id")
...     .order_by("user_id", desc("num_addresses"))
... )
>>> print(stmt)
{printsql}SELECT address.user_id, count(address.id) AS num_addresses
FROM address GROUP BY address.user_id ORDER BY address.user_id, num_addresses DESC
```

## Using Aliases {#tutorial_using_aliases}

Now that we are selecting from multiple tables and using joins, we quickly run into the case where we need to refer to the same table multiple times in the FROM clause of a statement. We accomplish this using SQL **aliases**, which are a syntax that supplies an alternative name to a table or subquery from which it can be referenced in the statement.

In the SQLAlchemy Expression Language, these \"names\" are instead represented by `_sql.FromClause`{.interpreted-text role="class"} objects known as the `_sql.Alias`{.interpreted-text role="class"} construct, which is constructed in Core using the `_sql.FromClause.alias`{.interpreted-text role="meth"} method. An `_sql.Alias`{.interpreted-text role="class"} construct is just like a `_sql.Table`{.interpreted-text role="class"} construct in that it also has a namespace of `_schema.Column`{.interpreted-text role="class"} objects within the `_sql.Alias.c`{.interpreted-text role="attr"} collection. The SELECT statement below for example returns all unique pairs of user names:

``` pycon+sql
>>> user_alias_1 = user_table.alias()
>>> user_alias_2 = user_table.alias()
>>> print(
...     select(user_alias_1.c.name, user_alias_2.c.name).join_from(
...         user_alias_1, user_alias_2, user_alias_1.c.id > user_alias_2.c.id
...     )
... )
{printsql}SELECT user_account_1.name, user_account_2.name AS name_1
FROM user_account AS user_account_1
JOIN user_account AS user_account_2 ON user_account_1.id > user_account_2.id
```

### ORM Entity Aliases {#tutorial_orm_entity_aliases}

The ORM equivalent of the `_sql.FromClause.alias`{.interpreted-text role="meth"} method is the ORM `_orm.aliased`{.interpreted-text role="func"} function, which may be applied to an entity such as `User` and `Address`. This produces a `_sql.Alias`{.interpreted-text role="class"} object internally that\'s against the original mapped `_schema.Table`{.interpreted-text role="class"} object, while maintaining ORM functionality. The SELECT below selects from the `User` entity all objects that include two particular email addresses:

``` pycon+sql
>>> from sqlalchemy.orm import aliased
>>> address_alias_1 = aliased(Address)
>>> address_alias_2 = aliased(Address)
>>> print(
...     select(User)
...     .join_from(User, address_alias_1)
...     .where(address_alias_1.email_address == "patrick@aol.com")
...     .join_from(User, address_alias_2)
...     .where(address_alias_2.email_address == "patrick@gmail.com")
... )
{printsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
JOIN address AS address_1 ON user_account.id = address_1.user_id
JOIN address AS address_2 ON user_account.id = address_2.user_id
WHERE address_1.email_address = :email_address_1
AND address_2.email_address = :email_address_2
```

:::: tip
::: title
Tip
:::

As mentioned in `tutorial_select_join_onclause`{.interpreted-text role="ref"}, the ORM provides for another way to join using the `_orm.relationship`{.interpreted-text role="func"} construct. The above example using aliases is demonstrated using `_orm.relationship`{.interpreted-text role="func"} at `tutorial_joining_relationships_aliased`{.interpreted-text role="ref"}.
::::

## Subqueries and CTEs {#tutorial_subqueries_ctes}

A subquery in SQL is a SELECT statement that is rendered within parenthesis and placed within the context of an enclosing statement, typically a SELECT statement but not necessarily.

This section will cover a so-called \"non-scalar\" subquery, which is typically placed in the FROM clause of an enclosing SELECT. We will also cover the Common Table Expression or CTE, which is used in a similar way as a subquery, but includes additional features.

SQLAlchemy uses the `_sql.Subquery`{.interpreted-text role="class"} object to represent a subquery and the `_sql.CTE`{.interpreted-text role="class"} to represent a CTE, usually obtained from the `_sql.Select.subquery`{.interpreted-text role="meth"} and `_sql.Select.cte`{.interpreted-text role="meth"} methods, respectively. Either object can be used as a FROM element inside of a larger `_sql.select`{.interpreted-text role="func"} construct.

We can construct a `_sql.Subquery`{.interpreted-text role="class"} that will select an aggregate count of rows from the `address` table (aggregate functions and GROUP BY were introduced previously at `tutorial_group_by_w_aggregates`{.interpreted-text role="ref"}):

> \>\>\> subq = ( \... select(func.count(address_table.c.id).label(\"count\"), address_table.c.user_id) \... .group_by(address_table.c.user_id) \... .subquery() \... )

Stringifying the subquery by itself without it being embedded inside of another `_sql.Select`{.interpreted-text role="class"} or other statement produces the plain SELECT statement without any enclosing parenthesis:

``` pycon+sql
>>> print(subq)
{printsql}SELECT count(address.id) AS count, address.user_id
FROM address GROUP BY address.user_id
```

The `_sql.Subquery`{.interpreted-text role="class"} object behaves like any other FROM object such as a `_schema.Table`{.interpreted-text role="class"}, notably that it includes a `_sql.Subquery.c`{.interpreted-text role="attr"} namespace of the columns which it selects. We can use this namespace to refer to both the `user_id` column as well as our custom labeled `count` expression:

``` pycon+sql
>>> print(select(subq.c.user_id, subq.c.count))
{printsql}SELECT anon_1.user_id, anon_1.count
FROM (SELECT count(address.id) AS count, address.user_id AS user_id
FROM address GROUP BY address.user_id) AS anon_1
```

With a selection of rows contained within the `subq` object, we can apply the object to a larger `_sql.Select`{.interpreted-text role="class"} that will join the data to the `user_account` table:

``` pycon+sql
>>> stmt = select(user_table.c.name, user_table.c.fullname, subq.c.count).join_from(
...     user_table, subq
... )

>>> print(stmt)
{printsql}SELECT user_account.name, user_account.fullname, anon_1.count
FROM user_account JOIN (SELECT count(address.id) AS count, address.user_id AS user_id
FROM address GROUP BY address.user_id) AS anon_1 ON user_account.id = anon_1.user_id
```

In order to join from `user_account` to `address`, we made use of the `_sql.Select.join_from`{.interpreted-text role="meth"} method. As has been illustrated previously, the ON clause of this join was again **inferred** based on foreign key constraints. Even though a SQL subquery does not itself have any constraints, SQLAlchemy can act upon constraints represented on the columns by determining that the `subq.c.user_id` column is **derived** from the `address_table.c.user_id` column, which does express a foreign key relationship back to the `user_table.c.id` column which is then used to generate the ON clause.

### Common Table Expressions (CTEs)

Usage of the `_sql.CTE`{.interpreted-text role="class"} construct in SQLAlchemy is virtually the same as how the `_sql.Subquery`{.interpreted-text role="class"} construct is used. By changing the invocation of the `_sql.Select.subquery`{.interpreted-text role="meth"} method to use `_sql.Select.cte`{.interpreted-text role="meth"} instead, we can use the resulting object as a FROM element in the same way, but the SQL rendered is the very different common table expression syntax:

``` pycon+sql
>>> subq = (
...     select(func.count(address_table.c.id).label("count"), address_table.c.user_id)
...     .group_by(address_table.c.user_id)
...     .cte()
... )

>>> stmt = select(user_table.c.name, user_table.c.fullname, subq.c.count).join_from(
...     user_table, subq
... )

>>> print(stmt)
{printsql}WITH anon_1 AS
(SELECT count(address.id) AS count, address.user_id AS user_id
FROM address GROUP BY address.user_id)
 SELECT user_account.name, user_account.fullname, anon_1.count
FROM user_account JOIN anon_1 ON user_account.id = anon_1.user_id
```

The `_sql.CTE`{.interpreted-text role="class"} construct also features the ability to be used in a \"recursive\" style, and may in more elaborate cases be composed from the RETURNING clause of an INSERT, UPDATE or DELETE statement. The docstring for `_sql.CTE`{.interpreted-text role="class"} includes details on these additional patterns.

In both cases, the subquery and CTE were named at the SQL level using an \"anonymous\" name. In the Python code, we don\'t need to provide these names at all. The object identity of the `_sql.Subquery`{.interpreted-text role="class"} or `_sql.CTE`{.interpreted-text role="class"} instances serves as the syntactical identity of the object when rendered. A name that will be rendered in the SQL can be provided by passing it as the first argument of the `_sql.Select.subquery`{.interpreted-text role="meth"} or `_sql.Select.cte`{.interpreted-text role="meth"} methods.

::: seealso
`_sql.Select.subquery`{.interpreted-text role="meth"} - further detail on subqueries

`_sql.Select.cte`{.interpreted-text role="meth"} - examples for CTE including how to use RECURSIVE as well as DML-oriented CTEs
:::

### ORM Entity Subqueries/CTEs {#tutorial_subqueries_orm_aliased}

In the ORM, the `_orm.aliased`{.interpreted-text role="func"} construct may be used to associate an ORM entity, such as our `User` or `Address` class, with any `_sql.FromClause`{.interpreted-text role="class"} concept that represents a source of rows. The preceding section `tutorial_orm_entity_aliases`{.interpreted-text role="ref"} illustrates using `_orm.aliased`{.interpreted-text role="func"} to associate the mapped class with an `_sql.Alias`{.interpreted-text role="class"} of its mapped `_schema.Table`{.interpreted-text role="class"}. Here we illustrate `_orm.aliased`{.interpreted-text role="func"} doing the same thing against both a `_sql.Subquery`{.interpreted-text role="class"} as well as a `_sql.CTE`{.interpreted-text role="class"} generated against a `_sql.Select`{.interpreted-text role="class"} construct, that ultimately derives from that same mapped `_schema.Table`{.interpreted-text role="class"}.

Below is an example of applying `_orm.aliased`{.interpreted-text role="func"} to the `_sql.Subquery`{.interpreted-text role="class"} construct, so that ORM entities can be extracted from its rows. The result shows a series of `User` and `Address` objects, where the data for each `Address` object ultimately came from a subquery against the `address` table rather than that table directly:

``` pycon+sql
>>> subq = select(Address).where(~Address.email_address.like("%@aol.com")).subquery()
>>> address_subq = aliased(Address, subq)
>>> stmt = (
...     select(User, address_subq)
...     .join_from(User, address_subq)
...     .order_by(User.id, address_subq.id)
... )
>>> with Session(engine) as session:
...     for user, address in session.execute(stmt):
...         print(f"{user} {address}")
{execsql}BEGIN (implicit)
SELECT user_account.id, user_account.name, user_account.fullname,
anon_1.id AS id_1, anon_1.email_address, anon_1.user_id
FROM user_account JOIN
(SELECT address.id AS id, address.email_address AS email_address, address.user_id AS user_id
FROM address
WHERE address.email_address NOT LIKE ?) AS anon_1 ON user_account.id = anon_1.user_id
ORDER BY user_account.id, anon_1.id
[...] ('%@aol.com',){stop}
User(id=1, name='spongebob', fullname='Spongebob Squarepants') Address(id=1, email_address='spongebob@sqlalchemy.org')
User(id=2, name='sandy', fullname='Sandy Cheeks') Address(id=2, email_address='sandy@sqlalchemy.org')
User(id=2, name='sandy', fullname='Sandy Cheeks') Address(id=3, email_address='sandy@squirrelpower.org')
{execsql}ROLLBACK{stop}
```

Another example follows, which is exactly the same except it makes use of the `_sql.CTE`{.interpreted-text role="class"} construct instead:

``` pycon+sql
>>> cte_obj = select(Address).where(~Address.email_address.like("%@aol.com")).cte()
>>> address_cte = aliased(Address, cte_obj)
>>> stmt = (
...     select(User, address_cte)
...     .join_from(User, address_cte)
...     .order_by(User.id, address_cte.id)
... )
>>> with Session(engine) as session:
...     for user, address in session.execute(stmt):
...         print(f"{user} {address}")
{execsql}BEGIN (implicit)
WITH anon_1 AS
(SELECT address.id AS id, address.email_address AS email_address, address.user_id AS user_id
FROM address
WHERE address.email_address NOT LIKE ?)
SELECT user_account.id, user_account.name, user_account.fullname,
anon_1.id AS id_1, anon_1.email_address, anon_1.user_id
FROM user_account
JOIN anon_1 ON user_account.id = anon_1.user_id
ORDER BY user_account.id, anon_1.id
[...] ('%@aol.com',){stop}
User(id=1, name='spongebob', fullname='Spongebob Squarepants') Address(id=1, email_address='spongebob@sqlalchemy.org')
User(id=2, name='sandy', fullname='Sandy Cheeks') Address(id=2, email_address='sandy@sqlalchemy.org')
User(id=2, name='sandy', fullname='Sandy Cheeks') Address(id=3, email_address='sandy@squirrelpower.org')
{execsql}ROLLBACK{stop}
```

::: seealso
`orm_queryguide_subqueries`{.interpreted-text role="ref"} - in the `queryguide_toplevel`{.interpreted-text role="ref"}
:::

## Scalar and Correlated Subqueries {#tutorial_scalar_subquery}

A scalar subquery is a subquery that returns exactly zero or one row and exactly one column. The subquery is then used in the COLUMNS or WHERE clause of an enclosing SELECT statement and is different than a regular subquery in that it is not used in the FROM clause. A `correlated subquery`{.interpreted-text role="term"} is a scalar subquery that refers to a table in the enclosing SELECT statement.

SQLAlchemy represents the scalar subquery using the `_sql.ScalarSelect`{.interpreted-text role="class"} construct, which is part of the `_sql.ColumnElement`{.interpreted-text role="class"} expression hierarchy, in contrast to the regular subquery which is represented by the `_sql.Subquery`{.interpreted-text role="class"} construct, which is in the `_sql.FromClause`{.interpreted-text role="class"} hierarchy.

Scalar subqueries are often, but not necessarily, used with aggregate functions, introduced previously at `tutorial_group_by_w_aggregates`{.interpreted-text role="ref"}. A scalar subquery is indicated explicitly by making use of the `_sql.Select.scalar_subquery`{.interpreted-text role="meth"} method as below. It\'s default string form when stringified by itself renders as an ordinary SELECT statement that is selecting from two tables:

``` pycon+sql
>>> subq = (
...     select(func.count(address_table.c.id))
...     .where(user_table.c.id == address_table.c.user_id)
...     .scalar_subquery()
... )
>>> print(subq)
{printsql}(SELECT count(address.id) AS count_1
FROM address, user_account
WHERE user_account.id = address.user_id)
```

The above `subq` object now falls within the `_sql.ColumnElement`{.interpreted-text role="class"} SQL expression hierarchy, in that it may be used like any other column expression:

``` pycon+sql
>>> print(subq == 5)
{printsql}(SELECT count(address.id) AS count_1
FROM address, user_account
WHERE user_account.id = address.user_id) = :param_1
```

Although the scalar subquery by itself renders both `user_account` and `address` in its FROM clause when stringified by itself, when embedding it into an enclosing `_sql.select`{.interpreted-text role="func"} construct that deals with the `user_account` table, the `user_account` table is automatically **correlated**, meaning it does not render in the FROM clause of the subquery:

``` pycon+sql
>>> stmt = select(user_table.c.name, subq.label("address_count"))
>>> print(stmt)
{printsql}SELECT user_account.name, (SELECT count(address.id) AS count_1
FROM address
WHERE user_account.id = address.user_id) AS address_count
FROM user_account
```

Simple correlated subqueries will usually do the right thing that\'s desired. However, in the case where the correlation is ambiguous, SQLAlchemy will let us know that more clarity is needed:

``` pycon+sql
>>> stmt = (
...     select(
...         user_table.c.name,
...         address_table.c.email_address,
...         subq.label("address_count"),
...     )
...     .join_from(user_table, address_table)
...     .order_by(user_table.c.id, address_table.c.id)
... )
>>> print(stmt)
Traceback (most recent call last):
...
InvalidRequestError: Select statement '<... Select object at ...>' returned
no FROM clauses due to auto-correlation; specify correlate(<tables>) to
control correlation manually.
```

To specify that the `user_table` is the one we seek to correlate we specify this using the `_sql.ScalarSelect.correlate`{.interpreted-text role="meth"} or `_sql.ScalarSelect.correlate_except`{.interpreted-text role="meth"} methods:

``` pycon+sql
>>> subq = (
...     select(func.count(address_table.c.id))
...     .where(user_table.c.id == address_table.c.user_id)
...     .scalar_subquery()
...     .correlate(user_table)
... )
```

The statement then can return the data for this column like any other:

``` pycon+sql
>>> with engine.connect() as conn:
...     result = conn.execute(
...         select(
...             user_table.c.name,
...             address_table.c.email_address,
...             subq.label("address_count"),
...         )
...         .join_from(user_table, address_table)
...         .order_by(user_table.c.id, address_table.c.id)
...     )
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT user_account.name, address.email_address, (SELECT count(address.id) AS count_1
FROM address
WHERE user_account.id = address.user_id) AS address_count
FROM user_account JOIN address ON user_account.id = address.user_id ORDER BY user_account.id, address.id
[...] (){stop}
[('spongebob', 'spongebob@sqlalchemy.org', 1), ('sandy', 'sandy@sqlalchemy.org', 2),
 ('sandy', 'sandy@squirrelpower.org', 2)]
{execsql}ROLLBACK{stop}
```

### LATERAL correlation {#tutorial_lateral_correlation}

LATERAL correlation is a special sub-category of SQL correlation which allows a selectable unit to refer to another selectable unit within a single FROM clause. This is an extremely special use case which, while part of the SQL standard, is only known to be supported by recent versions of PostgreSQL.

Normally, if a SELECT statement refers to `table1 JOIN (SELECT ...) AS subquery` in its FROM clause, the subquery on the right side may not refer to the \"table1\" expression from the left side; correlation may only refer to a table that is part of another SELECT that entirely encloses this SELECT. The LATERAL keyword allows us to turn this behavior around and allow correlation from the right side JOIN.

SQLAlchemy supports this feature using the `_expression.Select.lateral`{.interpreted-text role="meth"} method, which creates an object known as `.Lateral`{.interpreted-text role="class"}. `.Lateral`{.interpreted-text role="class"} is in the same family as `.Subquery`{.interpreted-text role="class"} and `.Alias`{.interpreted-text role="class"}, but also includes correlation behavior when the construct is added to the FROM clause of an enclosing SELECT. The following example illustrates a SQL query that makes use of LATERAL, selecting the \"user account / count of email address\" data as was discussed in the previous section:

``` pycon+sql
>>> subq = (
...     select(
...         func.count(address_table.c.id).label("address_count"),
...         address_table.c.email_address,
...         address_table.c.user_id,
...     )
...     .where(user_table.c.id == address_table.c.user_id)
...     .lateral()
... )
>>> stmt = (
...     select(user_table.c.name, subq.c.address_count, subq.c.email_address)
...     .join_from(user_table, subq)
...     .order_by(user_table.c.id, subq.c.email_address)
... )
>>> print(stmt)
{printsql}SELECT user_account.name, anon_1.address_count, anon_1.email_address
FROM user_account
JOIN LATERAL (SELECT count(address.id) AS address_count,
address.email_address AS email_address, address.user_id AS user_id
FROM address
WHERE user_account.id = address.user_id) AS anon_1
ON user_account.id = anon_1.user_id
ORDER BY user_account.id, anon_1.email_address
```

Above, the right side of the JOIN is a subquery that correlates to the `user_account` table that\'s on the left side of the join.

When using `_expression.Select.lateral`{.interpreted-text role="meth"}, the behavior of `_expression.Select.correlate`{.interpreted-text role="meth"} and `_expression.Select.correlate_except`{.interpreted-text role="meth"} methods is applied to the `.Lateral`{.interpreted-text role="class"} construct as well.

::: seealso
`_expression.Lateral`{.interpreted-text role="class"}

`_expression.Select.lateral`{.interpreted-text role="meth"}
:::

## UNION, UNION ALL and other set operations {#tutorial_union}

In SQL, SELECT statements can be merged together using the UNION or UNION ALL SQL operation, which produces the set of all rows produced by one or more statements together. Other set operations such as INTERSECT \[ALL\] and EXCEPT \[ALL\] are also possible.

SQLAlchemy\'s `_sql.Select`{.interpreted-text role="class"} construct supports compositions of this nature using functions like `_sql.union`{.interpreted-text role="func"}, `_sql.intersect`{.interpreted-text role="func"} and `_sql.except_`{.interpreted-text role="func"}, and the \"all\" counterparts `_sql.union_all`{.interpreted-text role="func"}, `_sql.intersect_all`{.interpreted-text role="func"} and `_sql.except_all`{.interpreted-text role="func"}. These functions all accept an arbitrary number of sub-selectables, which are typically `_sql.Select`{.interpreted-text role="class"} constructs but may also be an existing composition.

The construct produced by these functions is the `_sql.CompoundSelect`{.interpreted-text role="class"}, which is used in the same manner as the `_sql.Select`{.interpreted-text role="class"} construct, except that it has fewer methods. The `_sql.CompoundSelect`{.interpreted-text role="class"} produced by `_sql.union_all`{.interpreted-text role="func"} for example may be invoked directly using `_engine.Connection.execute`{.interpreted-text role="meth"}:

``` pycon+sql
>>> from sqlalchemy import union_all
>>> stmt1 = select(user_table).where(user_table.c.name == "sandy")
>>> stmt2 = select(user_table).where(user_table.c.name == "spongebob")
>>> u = union_all(stmt1, stmt2)
>>> with engine.connect() as conn:
...     result = conn.execute(u)
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
UNION ALL SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
[generated in ...] ('sandy', 'spongebob')
{stop}[(2, 'sandy', 'Sandy Cheeks'), (1, 'spongebob', 'Spongebob Squarepants')]
{execsql}ROLLBACK{stop}
```

To use a `_sql.CompoundSelect`{.interpreted-text role="class"} as a subquery, just like `_sql.Select`{.interpreted-text role="class"} it provides a `_sql.SelectBase.subquery`{.interpreted-text role="meth"} method which will produce a `_sql.Subquery`{.interpreted-text role="class"} object with a `_sql.FromClause.c`{.interpreted-text role="attr"} collection that may be referenced in an enclosing `_sql.select`{.interpreted-text role="func"}:

``` pycon+sql
>>> u_subq = u.subquery()
>>> stmt = (
...     select(u_subq.c.name, address_table.c.email_address)
...     .join_from(address_table, u_subq)
...     .order_by(u_subq.c.name, address_table.c.email_address)
... )
>>> with engine.connect() as conn:
...     result = conn.execute(stmt)
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT anon_1.name, address.email_address
FROM address JOIN
  (SELECT user_account.id AS id, user_account.name AS name, user_account.fullname AS fullname
  FROM user_account
  WHERE user_account.name = ?
UNION ALL
  SELECT user_account.id AS id, user_account.name AS name, user_account.fullname AS fullname
  FROM user_account
  WHERE user_account.name = ?)
AS anon_1 ON anon_1.id = address.user_id
ORDER BY anon_1.name, address.email_address
[generated in ...] ('sandy', 'spongebob')
{stop}[('sandy', 'sandy@sqlalchemy.org'), ('sandy', 'sandy@squirrelpower.org'), ('spongebob', 'spongebob@sqlalchemy.org')]
{execsql}ROLLBACK{stop}
```

### Selecting ORM Entities from Unions {#tutorial_orm_union}

The preceding examples illustrated how to construct a UNION given two `_schema.Table`{.interpreted-text role="class"} objects, to then return database rows. If we wanted to use a UNION or other set operation to select rows that we then receive as ORM objects, there are two approaches that may be used. In both cases, we first construct a `_sql.select`{.interpreted-text role="func"} or `_sql.CompoundSelect`{.interpreted-text role="class"} object that represents the SELECT / UNION / etc statement we want to execute; this statement should be composed against the target ORM entities or their underlying mapped `_schema.Table`{.interpreted-text role="class"} objects:

``` pycon+sql
>>> stmt1 = select(User).where(User.name == "sandy")
>>> stmt2 = select(User).where(User.name == "spongebob")
>>> u = union_all(stmt1, stmt2)
```

For a simple SELECT with UNION that is not already nested inside of a subquery, these can often be used in an ORM object fetching context by using the `_sql.Select.from_statement`{.interpreted-text role="meth"} method. With this approach, the UNION statement represents the entire query; no additional criteria can be added after `_sql.Select.from_statement`{.interpreted-text role="meth"} is used:

``` pycon+sql
>>> orm_stmt = select(User).from_statement(u)
>>> with Session(engine) as session:
...     for obj in session.execute(orm_stmt).scalars():
...         print(obj)
{execsql}BEGIN (implicit)
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ? UNION ALL SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
[generated in ...] ('sandy', 'spongebob')
{stop}User(id=2, name='sandy', fullname='Sandy Cheeks')
User(id=1, name='spongebob', fullname='Spongebob Squarepants')
{execsql}ROLLBACK{stop}
```

To use a UNION or other set-related construct as an entity-related component in in a more flexible manner, the `_sql.CompoundSelect`{.interpreted-text role="class"} construct may be organized into a subquery using `_sql.CompoundSelect.subquery`{.interpreted-text role="meth"}, which then links to ORM objects using the `_orm.aliased`{.interpreted-text role="func"} function. This works in the same way introduced at `tutorial_subqueries_orm_aliased`{.interpreted-text role="ref"}, to first create an ad-hoc \"mapping\" of our desired entity to the subquery, then selecting from that new entity as though it were any other mapped class. In the example below, we are able to add additional criteria such as ORDER BY outside of the UNION itself, as we can filter or order by the columns exported by the subquery:

``` pycon+sql
>>> user_alias = aliased(User, u.subquery())
>>> orm_stmt = select(user_alias).order_by(user_alias.id)
>>> with Session(engine) as session:
...     for obj in session.execute(orm_stmt).scalars():
...         print(obj)
{execsql}BEGIN (implicit)
SELECT anon_1.id, anon_1.name, anon_1.fullname
FROM (SELECT user_account.id AS id, user_account.name AS name, user_account.fullname AS fullname
FROM user_account
WHERE user_account.name = ? UNION ALL SELECT user_account.id AS id, user_account.name AS name, user_account.fullname AS fullname
FROM user_account
WHERE user_account.name = ?) AS anon_1 ORDER BY anon_1.id
[generated in ...] ('sandy', 'spongebob')
{stop}User(id=1, name='spongebob', fullname='Spongebob Squarepants')
User(id=2, name='sandy', fullname='Sandy Cheeks')
{execsql}ROLLBACK{stop}
```

::: seealso
`orm_queryguide_unions`{.interpreted-text role="ref"} - in the `queryguide_toplevel`{.interpreted-text role="ref"}
:::

## EXISTS subqueries {#tutorial_exists}

The SQL EXISTS keyword is an operator that is used with `scalar subqueries
<tutorial_scalar_subquery>`{.interpreted-text role="ref"} to return a boolean true or false depending on if the SELECT statement would return a row. SQLAlchemy includes a variant of the `_sql.ScalarSelect`{.interpreted-text role="class"} object called `_sql.Exists`{.interpreted-text role="class"}, which will generate an EXISTS subquery and is most conveniently generated using the `_sql.SelectBase.exists`{.interpreted-text role="meth"} method. Below we produce an EXISTS so that we can return `user_account` rows that have more than one related row in `address`:

``` pycon+sql
>>> subq = (
...     select(func.count(address_table.c.id))
...     .where(user_table.c.id == address_table.c.user_id)
...     .group_by(address_table.c.user_id)
...     .having(func.count(address_table.c.id) > 1)
... ).exists()
>>> with engine.connect() as conn:
...     result = conn.execute(select(user_table.c.name).where(subq))
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT user_account.name
FROM user_account
WHERE EXISTS (SELECT count(address.id) AS count_1
FROM address
WHERE user_account.id = address.user_id GROUP BY address.user_id
HAVING count(address.id) > ?)
[...] (1,){stop}
[('sandy',)]
{execsql}ROLLBACK{stop}
```

The EXISTS construct is more often than not used as a negation, e.g. NOT EXISTS, as it provides a SQL-efficient form of locating rows for which a related table has no rows. Below we select user names that have no email addresses; note the binary negation operator (`~`) used inside the second WHERE clause:

``` pycon+sql
>>> subq = (
...     select(address_table.c.id).where(user_table.c.id == address_table.c.user_id)
... ).exists()
>>> with engine.connect() as conn:
...     result = conn.execute(select(user_table.c.name).where(~subq))
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT user_account.name
FROM user_account
WHERE NOT (EXISTS (SELECT address.id
FROM address
WHERE user_account.id = address.user_id))
[...] (){stop}
[('patrick',)]
{execsql}ROLLBACK{stop}
```

## Working with SQL Functions {#tutorial_functions}

First introduced earlier in this section at `tutorial_group_by_w_aggregates`{.interpreted-text role="ref"}, the `_sql.func`{.interpreted-text role="data"} object serves as a factory for creating new `_functions.Function`{.interpreted-text role="class"} objects, which when used in a construct like `_sql.select`{.interpreted-text role="func"}, produce a SQL function display, typically consisting of a name, some parenthesis (although not always), and possibly some arguments. Examples of typical SQL functions include:

- the `count()` function, an aggregate function which counts how many rows are returned:

  ``` pycon+sql
  >>> print(select(func.count()).select_from(user_table))
  {printsql}SELECT count(*) AS count_1
  FROM user_account
  ```

- the `lower()` function, a string function that converts a string to lower case:

  ``` pycon+sql
  >>> print(select(func.lower("A String With Much UPPERCASE")))
  {printsql}SELECT lower(:lower_2) AS lower_1
  ```

- the `now()` function, which provides for the current date and time; as this is a common function, SQLAlchemy knows how to render this differently for each backend, in the case of SQLite using the CURRENT_TIMESTAMP function:

  ``` pycon+sql
  >>> stmt = select(func.now())
  >>> with engine.connect() as conn:
  ...     result = conn.execute(stmt)
  ...     print(result.all())
  {execsql}BEGIN (implicit)
  SELECT CURRENT_TIMESTAMP AS now_1
  [...] ()
  [(datetime.datetime(...),)]
  ROLLBACK
  ```

As most database backends feature dozens if not hundreds of different SQL functions, `_sql.func`{.interpreted-text role="data"} tries to be as liberal as possible in what it accepts. Any name that is accessed from this namespace is automatically considered to be a SQL function that will render in a generic way:

``` pycon+sql
>>> print(select(func.some_crazy_function(user_table.c.name, 17)))
{printsql}SELECT some_crazy_function(user_account.name, :some_crazy_function_2) AS some_crazy_function_1
FROM user_account
```

At the same time, a relatively small set of extremely common SQL functions such as `_functions.count`{.interpreted-text role="class"}, `_functions.now`{.interpreted-text role="class"}, `_functions.max`{.interpreted-text role="class"}, `_functions.concat`{.interpreted-text role="class"} include pre-packaged versions of themselves which provide for proper typing information as well as backend-specific SQL generation in some cases. The example below contrasts the SQL generation that occurs for the PostgreSQL dialect compared to the Oracle Database dialect for the `_functions.now`{.interpreted-text role="class"} function:

``` pycon+sql
>>> from sqlalchemy.dialects import postgresql
>>> print(select(func.now()).compile(dialect=postgresql.dialect()))
{printsql}SELECT now() AS now_1{stop}
>>> from sqlalchemy.dialects import oracle
>>> print(select(func.now()).compile(dialect=oracle.dialect()))
{printsql}SELECT CURRENT_TIMESTAMP AS now_1 FROM DUAL{stop}
```

### Functions Have Return Types

As functions are column expressions, they also have SQL `datatypes <types_toplevel>`{.interpreted-text role="ref"} that describe the data type of a generated SQL expression. We refer to these types here as \"SQL return types\", in reference to the type of SQL value that is returned by the function in the context of a database-side SQL expression, as opposed to the \"return type\" of a Python function.

The SQL return type of any SQL function may be accessed, typically for debugging purposes, by referring to the `_functions.Function.type`{.interpreted-text role="attr"} attribute; this will be pre-configured for a **select few** of extremely common SQL functions, but for most SQL functions is the \"null\" datatype if not otherwise specified:

``` pycon+sql
>>> # pre-configured SQL function (only a few dozen of these)
>>> func.now().type
DateTime()

>>> # arbitrary SQL function (all other SQL functions)
>>> func.run_some_calculation().type
NullType()
```

These SQL return types are significant when making use of the function expression in the context of a larger expression; that is, math operators will work better when the datatype of the expression is something like `_types.Integer`{.interpreted-text role="class"} or `_types.Numeric`{.interpreted-text role="class"}, JSON accessors in order to work need to be using a type such as `_types.JSON`{.interpreted-text role="class"}. Certain classes of functions return entire rows instead of column values, where there is a need to refer to specific columns; such functions are known as `table valued functions <tutorial_functions_table_valued>`{.interpreted-text role="ref"}.

The SQL return type of the function may also be significant when executing a statement and getting rows back, for those cases where SQLAlchemy has to apply result-set processing. A prime example of this are date-related functions on SQLite, where SQLAlchemy\'s `_types.DateTime`{.interpreted-text role="class"} and related datatypes take on the role of converting from string values to Python `datetime()` objects as result rows are received.

To apply a specific type to a function we\'re creating, we pass it using the `_functions.Function.type_`{.interpreted-text role="paramref"} parameter; the type argument may be either a `_types.TypeEngine`{.interpreted-text role="class"} class or an instance. In the example below we pass the `_types.JSON`{.interpreted-text role="class"} class to generate the PostgreSQL `json_object()` function, noting that the SQL return type will be of type JSON:

``` pycon+sql
>>> from sqlalchemy import JSON
>>> function_expr = func.json_object('{a, 1, b, "def", c, 3.5}', type_=JSON)
```

By creating our JSON function with the `_types.JSON`{.interpreted-text role="class"} datatype, the SQL expression object takes on JSON-related features, such as that of accessing elements:

``` pycon+sql
>>> stmt = select(function_expr["def"])
>>> print(stmt)
{printsql}SELECT json_object(:json_object_1)[:json_object_2] AS anon_1
```

### Built-in Functions Have Pre-Configured Return Types

For common aggregate functions like `_functions.count`{.interpreted-text role="class"}, `_functions.max`{.interpreted-text role="class"}, `_functions.min`{.interpreted-text role="class"} as well as a very small number of date functions like `_functions.now`{.interpreted-text role="class"} and string functions like `_functions.concat`{.interpreted-text role="class"}, the SQL return type is set up appropriately, sometimes based on usage. The `_functions.max`{.interpreted-text role="class"} function and similar aggregate filtering functions will set up the SQL return type based on the argument given:

``` pycon+sql
>>> m1 = func.max(Column("some_int", Integer))
>>> m1.type
Integer()

>>> m2 = func.max(Column("some_str", String))
>>> m2.type
String()
```

Date and time functions typically correspond to SQL expressions described by `_types.DateTime`{.interpreted-text role="class"}, `_types.Date`{.interpreted-text role="class"} or `_types.Time`{.interpreted-text role="class"}:

``` pycon+sql
>>> func.now().type
DateTime()
>>> func.current_date().type
Date()
```

A known string function such as `_functions.concat`{.interpreted-text role="class"} will know that a SQL expression would be of type `_types.String`{.interpreted-text role="class"}:

``` pycon+sql
>>> func.concat("x", "y").type
String()
```

However, for the vast majority of SQL functions, SQLAlchemy does not have them explicitly present in its very small list of known functions. For example, while there is typically no issue using SQL functions `func.lower()` and `func.upper()` to convert the casing of strings, SQLAlchemy doesn\'t actually know about these functions, so they have a \"null\" SQL return type:

``` pycon+sql
>>> func.upper("lowercase").type
NullType()
```

For simple functions like `upper` and `lower`, the issue is not usually significant, as string values may be received from the database without any special type handling on the SQLAlchemy side, and SQLAlchemy\'s type coercion rules can often correctly guess intent as well; the Python `+` operator for example will be correctly interpreted as the string concatenation operator based on looking at both sides of the expression:

``` pycon+sql
>>> print(select(func.upper("lowercase") + " suffix"))
{printsql}SELECT upper(:upper_1) || :upper_2 AS anon_1
```

Overall, the scenario where the `_functions.Function.type_`{.interpreted-text role="paramref"} parameter is likely necessary is:

1.  the function is not already a SQLAlchemy built-in function; this can be evidenced by creating the function and observing the `_functions.Function.type`{.interpreted-text role="attr"} attribute, that is:

    ``` pycon+sql
    >>> func.count().type
    Integer()
    ```

    vs.:

    ``` pycon+sql
    >>> func.json_object('{"a", "b"}').type
    NullType()
    ```

2.  Function-aware expression support is needed; this most typically refers to special operators related to datatypes such as `_types.JSON`{.interpreted-text role="class"} or `_types.ARRAY`{.interpreted-text role="class"}

3.  Result value processing is needed, which may include types such as `_functions.DateTime`{.interpreted-text role="class"}, `_types.Boolean`{.interpreted-text role="class"}, `_types.Enum`{.interpreted-text role="class"}, or again special datatypes such as `_types.JSON`{.interpreted-text role="class"}, `_types.ARRAY`{.interpreted-text role="class"}.

### Advanced SQL Function Techniques

The following subsections illustrate more things that can be done with SQL functions. While these techniques are less common and more advanced than basic SQL function use, they nonetheless are extremely popular, largely as a result of PostgreSQL\'s emphasis on more complex function forms, including table- and column-valued forms that are popular with JSON data.

#### Using Window Functions {#tutorial_window_functions}

A window function is a special use of a SQL aggregate function which calculates the aggregate value over the rows being returned in a group as the individual result rows are processed. Whereas a function like `MAX()` will give you the highest value of a column within a set of rows, using the same function as a \"window function\" will given you the highest value for each row, *as of that row*.

In SQL, window functions allow one to specify the rows over which the function should be applied, a \"partition\" value which considers the window over different sub-sets of rows, and an \"order by\" expression which importantly indicates the order in which rows should be applied to the aggregate function.

In SQLAlchemy, all SQL functions generated by the `_sql.func`{.interpreted-text role="data"} namespace include a method `_functions.FunctionElement.over`{.interpreted-text role="meth"} which grants the window function, or \"OVER\", syntax; the construct produced is the `_sql.Over`{.interpreted-text role="class"} construct.

A common function used with window functions is the `row_number()` function which simply counts rows. We may partition this row count against user name to number the email addresses of individual users:

``` pycon+sql
>>> stmt = (
...     select(
...         func.row_number().over(partition_by=user_table.c.name),
...         user_table.c.name,
...         address_table.c.email_address,
...     )
...     .select_from(user_table)
...     .join(address_table)
... )
>>> with engine.connect() as conn:  # doctest:+SKIP
...     result = conn.execute(stmt)
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT row_number() OVER (PARTITION BY user_account.name) AS anon_1,
user_account.name, address.email_address
FROM user_account JOIN address ON user_account.id = address.user_id
[...] ()
{stop}[(1, 'sandy', 'sandy@sqlalchemy.org'), (2, 'sandy', 'sandy@squirrelpower.org'), (1, 'spongebob', 'spongebob@sqlalchemy.org')]
{printsql}ROLLBACK{stop}
```

Above, the `_functions.FunctionElement.over.partition_by`{.interpreted-text role="paramref"} parameter is used so that the `PARTITION BY` clause is rendered within the OVER clause. We also may make use of the `ORDER BY` clause using `_functions.FunctionElement.over.order_by`{.interpreted-text role="paramref"}:

``` pycon+sql
>>> stmt = (
...     select(
...         func.count().over(order_by=user_table.c.name),
...         user_table.c.name,
...         address_table.c.email_address,
...     )
...     .select_from(user_table)
...     .join(address_table)
... )
>>> with engine.connect() as conn:  # doctest:+SKIP
...     result = conn.execute(stmt)
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT count(*) OVER (ORDER BY user_account.name) AS anon_1,
user_account.name, address.email_address
FROM user_account JOIN address ON user_account.id = address.user_id
[...] ()
{stop}[(2, 'sandy', 'sandy@sqlalchemy.org'), (2, 'sandy', 'sandy@squirrelpower.org'), (3, 'spongebob', 'spongebob@sqlalchemy.org')]
{printsql}ROLLBACK{stop}
```

Further options for window functions include usage of ranges; see `_expression.over`{.interpreted-text role="func"} for more examples.

:::: tip
::: title
Tip
:::

It\'s important to note that the `_functions.FunctionElement.over`{.interpreted-text role="meth"} method only applies to those SQL functions which are in fact aggregate functions; while the `_sql.Over`{.interpreted-text role="class"} construct will happily render itself for any SQL function given, the database will reject the expression if the function itself is not a SQL aggregate function.
::::

#### Special Modifiers ORDER BY, WITHIN GROUP, FILTER {#tutorial_functions_within_group}

Some forms of SQL aggregate functions support ordering of the aggregated elements within the scope of the function. This typically applies to aggregate functions that produce a value which continues to enumerate the contents of the collection, such as the `array_agg()` function that generates an array of elements, or the `string_agg()` PostgreSQL function which generates a delimited string (other backends like MySQL and SQLite use the `group_concat()` function in a similar way), or the MySQL `json_arrayagg()` function which produces a JSON array. Ordering of the elements passed to these functions is supported using the `_functions.FunctionElement.aggregate_order_by`{.interpreted-text role="meth"} method, which will render ORDER BY in the appropriate part of the function:

``` pycon+sql
>>> stmt = select(
...     func.group_concat(user_table.c.name).aggregate_order_by(user_table.c.name.desc())
... )
>>> print(stmt)
{printsql}SELECT group_concat(user_account.name ORDER BY user_account.name DESC) AS group_concat_1
FROM user_account
```

:::: tip
::: title
Tip
:::

The above demonstration shows use of the `group_concat()` function available on SQLite which concatenates strings; the ORDER BY feature for SQLite requires SQLite 3.44.0 or greater. As the availability, name and specific syntax of the string aggregation functions varies widely by backend, SQLAlchemy also provides a backend-agnostic version specifically for concatenating strings called `_functions.aggregate_strings`{.interpreted-text role="func"}.
::::

A more specific form of ORDER BY for aggregate functions is the \"WITHIN GROUP\" SQL syntax. In some cases, the `_functions.FunctionElement.aggregate_order_by`{.interpreted-text role="meth"} will render this syntax directly, when compiling on a backend such as Oracle Database or Microsoft SQL Server which requires it for all aggregate ordering. Beyond that, the \"WITHIN GROUP\" SQL syntax must sometimes be called upon explicitly, when used in conjunction with an \"ordered set\" or a \"hypothetical set\" aggregate function, supported by PostgreSQL, Oracle Database, and Microsoft SQL Server. Common \"ordered set\" functions include `percentile_cont()` and `rank()`. SQLAlchemy includes built in implementations `_functions.rank`{.interpreted-text role="class"}, `_functions.dense_rank`{.interpreted-text role="class"}, `_functions.mode`{.interpreted-text role="class"}, `_functions.percentile_cont`{.interpreted-text role="class"} and `_functions.percentile_disc`{.interpreted-text role="class"} which include a `_functions.FunctionElement.within_group`{.interpreted-text role="meth"} method:

``` pycon+sql
>>> print(
...     func.unnest(
...         func.percentile_disc([0.25, 0.5, 0.75, 1]).within_group(user_table.c.name)
...     )
... )
{printsql}unnest(percentile_disc(:percentile_disc_1) WITHIN GROUP (ORDER BY user_account.name))
```

\"FILTER\" is supported by some backends to limit the range of an aggregate function to a particular subset of rows compared to the total range of rows returned, available using the `_functions.FunctionElement.filter`{.interpreted-text role="meth"} method:

``` pycon+sql
>>> stmt = (
...     select(
...         func.count(address_table.c.email_address).filter(user_table.c.name == "sandy"),
...         func.count(address_table.c.email_address).filter(
...             user_table.c.name == "spongebob"
...         ),
...     )
...     .select_from(user_table)
...     .join(address_table)
... )
>>> with engine.connect() as conn:  # doctest:+SKIP
...     result = conn.execute(stmt)
...     print(result.all())
{execsql}BEGIN (implicit)
SELECT count(address.email_address) FILTER (WHERE user_account.name = ?) AS anon_1,
count(address.email_address) FILTER (WHERE user_account.name = ?) AS anon_2
FROM user_account JOIN address ON user_account.id = address.user_id
[...] ('sandy', 'spongebob')
{stop}[(2, 1)]
{execsql}ROLLBACK
```

#### Table-Valued Functions {#tutorial_functions_table_valued}

Table-valued SQL functions support a scalar representation that contains named sub-elements. Often used for JSON and ARRAY-oriented functions as well as functions like `generate_series()`, the table-valued function is specified in the FROM clause, and is then referenced as a table, or sometimes even as a column. Functions of this form are prominent within the PostgreSQL database, however some forms of table valued functions are also supported by SQLite, Oracle Database, and SQL Server.

::: seealso
`postgresql_table_valued_overview`{.interpreted-text role="ref"} - in the `postgresql_toplevel`{.interpreted-text role="ref"} documentation.

While many databases support table valued and other special forms, PostgreSQL tends to be where there is the most demand for these features. See this section for additional examples of PostgreSQL syntaxes as well as additional features.
:::

SQLAlchemy provides the `_functions.FunctionElement.table_valued`{.interpreted-text role="meth"} method as the basic \"table valued function\" construct, which will convert a `_sql.func`{.interpreted-text role="data"} object into a FROM clause containing a series of named columns, based on string names passed positionally. This returns a `_sql.TableValuedAlias`{.interpreted-text role="class"} object, which is a function-enabled `_sql.Alias`{.interpreted-text role="class"} construct that may be used as any other FROM clause as introduced at `tutorial_using_aliases`{.interpreted-text role="ref"}. Below we illustrate the `json_each()` function, which while common on PostgreSQL is also supported by modern versions of SQLite:

``` pycon+sql
>>> onetwothree = func.json_each('["one", "two", "three"]').table_valued("value")
>>> stmt = select(onetwothree).where(onetwothree.c.value.in_(["two", "three"]))
>>> with engine.connect() as conn:
...     result = conn.execute(stmt)
...     result.all()
{execsql}BEGIN (implicit)
SELECT anon_1.value
FROM json_each(?) AS anon_1
WHERE anon_1.value IN (?, ?)
[...] ('["one", "two", "three"]', 'two', 'three')
{stop}[('two',), ('three',)]
{execsql}ROLLBACK{stop}
```

Above, we used the `json_each()` JSON function supported by SQLite and PostgreSQL to generate a table valued expression with a single column referred towards as `value`, and then selected two of its three rows.

::: seealso
`postgresql_table_valued`{.interpreted-text role="ref"} - in the `postgresql_toplevel`{.interpreted-text role="ref"} documentation - this section will detail additional syntaxes such as special column derivations and \"WITH ORDINALITY\" that are known to work with PostgreSQL.
:::

#### Column Valued Functions - Table Valued Function as a Scalar Column {#tutorial_functions_column_valued}

A special syntax supported by PostgreSQL and Oracle Database is that of referring towards a function in the FROM clause, which then delivers itself as a single column in the columns clause of a SELECT statement or other column expression context. PostgreSQL makes great use of this syntax for such functions as `json_array_elements()`, `json_object_keys()`, `json_each_text()`, `json_each()`, etc.

SQLAlchemy refers to this as a \"column valued\" function and is available by applying the `_functions.FunctionElement.column_valued`{.interpreted-text role="meth"} modifier to a `_functions.Function`{.interpreted-text role="class"} construct:

``` pycon+sql
>>> from sqlalchemy import select, func
>>> stmt = select(func.json_array_elements('["one", "two"]').column_valued("x"))
>>> print(stmt)
{printsql}SELECT x
FROM json_array_elements(:json_array_elements_1) AS x
```

The \"column valued\" form is also supported by the Oracle Database dialects, where it is usable for custom SQL functions:

``` pycon+sql
>>> from sqlalchemy.dialects import oracle
>>> stmt = select(func.scalar_strings(5).column_valued("s"))
>>> print(stmt.compile(dialect=oracle.dialect()))
{printsql}SELECT s.COLUMN_VALUE
FROM TABLE (scalar_strings(:scalar_strings_1)) s
```

::: seealso
`postgresql_column_valued`{.interpreted-text role="ref"} - in the `postgresql_toplevel`{.interpreted-text role="ref"} documentation.
:::

## Data Casts and Type Coercion {#tutorial_casts}

In SQL, we often need to indicate the datatype of an expression explicitly, either to tell the database what type is expected in an otherwise ambiguous expression, or in some cases when we want to convert the implied datatype of a SQL expression into something else. The SQL CAST keyword is used for this task, which in SQLAlchemy is provided by the `.cast`{.interpreted-text role="func"} function. This function accepts a column expression and a data type object as arguments, as demonstrated below where we produce a SQL expression `CAST(user_account.id AS VARCHAR)` from the `user_table.c.id` column object:

``` pycon+sql
>>> from sqlalchemy import cast
>>> stmt = select(cast(user_table.c.id, String))
>>> with engine.connect() as conn:
...     result = conn.execute(stmt)
...     result.all()
{execsql}BEGIN (implicit)
SELECT CAST(user_account.id AS VARCHAR) AS id
FROM user_account
[...] ()
{stop}[('1',), ('2',), ('3',)]
{execsql}ROLLBACK{stop}
```

The `.cast`{.interpreted-text role="func"} function not only renders the SQL CAST syntax, it also produces a SQLAlchemy column expression that will act as the given datatype on the Python side as well. A string expression that is `.cast`{.interpreted-text role="func"} to `_sqltypes.JSON`{.interpreted-text role="class"} will gain JSON subscript and comparison operators, for example:

``` pycon+sql
>>> from sqlalchemy import JSON
>>> print(cast("{'a': 'b'}", JSON)["a"])
{printsql}CAST(:param_1 AS JSON)[:param_2]
```

### type_coerce() - a Python-only \"cast\"

Sometimes there is the need to have SQLAlchemy know the datatype of an expression, for all the reasons mentioned above, but to not render the CAST expression itself on the SQL side, where it may interfere with a SQL operation that already works without it. For this fairly common use case there is another function `.type_coerce`{.interpreted-text role="func"} which is closely related to `.cast`{.interpreted-text role="func"}, in that it sets up a Python expression as having a specific SQL database type, but does not render the `CAST` keyword or datatype on the database side. `.type_coerce`{.interpreted-text role="func"} is particularly important when dealing with the `_types.JSON`{.interpreted-text role="class"} datatype, which typically has an intricate relationship with string-oriented datatypes on different platforms and may not even be an explicit datatype, such as on SQLite and MariaDB. Below, we use `.type_coerce`{.interpreted-text role="func"} to deliver a Python structure as a JSON string into one of MySQL\'s JSON functions:

``` pycon+sql
>>> import json
>>> from sqlalchemy import JSON
>>> from sqlalchemy import type_coerce
>>> from sqlalchemy.dialects import mysql
>>> s = select(type_coerce({"some_key": {"foo": "bar"}}, JSON)["some_key"])
>>> print(s.compile(dialect=mysql.dialect()))
{printsql}SELECT JSON_EXTRACT(%s, %s) AS anon_1
```

Above, MySQL\'s `JSON_EXTRACT` SQL function was invoked because we used `.type_coerce`{.interpreted-text role="func"} to indicate that our Python dictionary should be treated as `_types.JSON`{.interpreted-text role="class"}. The Python `__getitem__` operator, `['some_key']` in this case, became available as a result and allowed a `JSON_EXTRACT` path expression (not shown, however in this case it would ultimately be `'$."some_key"'`) to be rendered.
