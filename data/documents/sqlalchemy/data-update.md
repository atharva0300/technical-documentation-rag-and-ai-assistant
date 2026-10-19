::: rst-class
core-header, orm-addin
:::

# Using UPDATE and DELETE Statements {#tutorial_core_update_delete}

So far we\'ve covered `_sql.Insert`{.interpreted-text role="class"}, so that we can get some data into our database, and then spent a lot of time on `_sql.Select`{.interpreted-text role="class"} which handles the broad range of usage patterns used for retrieving data from the database. In this section we will cover the `_sql.Update`{.interpreted-text role="class"} and `_sql.Delete`{.interpreted-text role="class"} constructs, which are used to modify existing rows as well as delete existing rows. This section will cover these constructs from a Core-centric perspective.

::: {.container .orm-header}
**ORM Readers** - As was the case mentioned at `tutorial_core_insert`{.interpreted-text role="ref"}, the `_sql.Update`{.interpreted-text role="class"} and `_sql.Delete`{.interpreted-text role="class"} operations when used with the ORM are usually invoked internally from the `_orm.Session`{.interpreted-text role="class"} object as part of the `unit of work`{.interpreted-text role="term"} process.

However, unlike `_sql.Insert`{.interpreted-text role="class"}, the `_sql.Update`{.interpreted-text role="class"} and `_sql.Delete`{.interpreted-text role="class"} constructs can also be used directly with the ORM, using a pattern known as \"ORM-enabled update and delete\"; for this reason, familiarity with these constructs is useful for ORM use. Both styles of use are discussed in the sections `tutorial_orm_updating`{.interpreted-text role="ref"} and `tutorial_orm_deleting`{.interpreted-text role="ref"}.
:::

## The update() SQL Expression Construct {#tutorial_core_update}

The `_sql.update`{.interpreted-text role="func"} function generates a new instance of `_sql.Update`{.interpreted-text role="class"} which represents an UPDATE statement in SQL, that will update existing data in a table.

Like the `_sql.insert`{.interpreted-text role="func"} construct, there is a \"traditional\" form of `_sql.update`{.interpreted-text role="func"}, which emits UPDATE against a single table at a time and does not return any rows. However some backends support an UPDATE statement that may modify multiple tables at once, and the UPDATE statement also supports RETURNING such that columns contained in matched rows may be returned in the result set.

A basic UPDATE looks like:

``` pycon+sql
>>> from sqlalchemy import update
>>> stmt = (
...     update(user_table)
...     .where(user_table.c.name == "patrick")
...     .values(fullname="Patrick the Star")
... )
>>> print(stmt)
{printsql}UPDATE user_account SET fullname=:fullname WHERE user_account.name = :name_1
```

The `_sql.Update.values`{.interpreted-text role="meth"} method controls the contents of the SET elements of the UPDATE statement. This is the same method shared by the `_sql.Insert`{.interpreted-text role="class"} construct. Parameters can normally be passed using the column names as keyword arguments.

UPDATE supports all the major SQL forms of UPDATE, including updates against expressions, where we can make use of `_schema.Column`{.interpreted-text role="class"} expressions:

``` pycon+sql
>>> stmt = update(user_table).values(fullname="Username: " + user_table.c.name)
>>> print(stmt)
{printsql}UPDATE user_account SET fullname=(:name_1 || user_account.name)
```

To support UPDATE in an \"executemany\" context, where many parameter sets will be invoked against the same statement, the `_sql.bindparam`{.interpreted-text role="func"} construct may be used to set up bound parameters; these replace the places that literal values would normally go:

``` pycon+sql
>>> from sqlalchemy import bindparam
>>> stmt = (
...     update(user_table)
...     .where(user_table.c.name == bindparam("oldname"))
...     .values(name=bindparam("newname"))
... )
>>> with engine.begin() as conn:
...     conn.execute(
...         stmt,
...         [
...             {"oldname": "jack", "newname": "ed"},
...             {"oldname": "wendy", "newname": "mary"},
...             {"oldname": "jim", "newname": "jake"},
...         ],
...     )
{execsql}BEGIN (implicit)
UPDATE user_account SET name=? WHERE user_account.name = ?
[...] [('ed', 'jack'), ('mary', 'wendy'), ('jake', 'jim')]
<sqlalchemy.engine.cursor.CursorResult object at 0x...>
COMMIT{stop}
```

Other techniques which may be applied to UPDATE include:

### Correlated Updates {#tutorial_correlated_updates}

An UPDATE statement can make use of rows in other tables by using a `correlated subquery <tutorial_scalar_subquery>`{.interpreted-text role="ref"}. A subquery may be used anywhere a column expression might be placed:

``` pycon+sql
>>> scalar_subq = (
...     select(address_table.c.email_address)
...     .where(address_table.c.user_id == user_table.c.id)
...     .order_by(address_table.c.id)
...     .limit(1)
...     .scalar_subquery()
... )
>>> update_stmt = update(user_table).values(fullname=scalar_subq)
>>> print(update_stmt)
{printsql}UPDATE user_account SET fullname=(SELECT address.email_address
FROM address
WHERE address.user_id = user_account.id ORDER BY address.id
LIMIT :param_1)
```

### UPDATE..FROM {#tutorial_update_from}

Some databases such as PostgreSQL, MSSQL and MySQL support a syntax `UPDATE...FROM` where additional tables may be stated directly in a special FROM clause. This syntax will be generated implicitly when additional tables are located in the WHERE clause of the statement:

``` pycon+sql
>>> update_stmt = (
...     update(user_table)
...     .where(user_table.c.id == address_table.c.user_id)
...     .where(address_table.c.email_address == "patrick@aol.com")
...     .values(fullname="Pat")
... )
>>> print(update_stmt)
{printsql}UPDATE user_account SET fullname=:fullname FROM address
WHERE user_account.id = address.user_id AND address.email_address = :email_address_1
```

There is also a MySQL specific syntax that can UPDATE multiple tables. This requires we refer to `_schema.Table`{.interpreted-text role="class"} objects in the VALUES clause in order to refer to additional tables:

``` pycon+sql
>>> update_stmt = (
...     update(user_table)
...     .where(user_table.c.id == address_table.c.user_id)
...     .where(address_table.c.email_address == "patrick@aol.com")
...     .values(
...         {
...             user_table.c.fullname: "Pat",
...             address_table.c.email_address: "pat@aol.com",
...         }
...     )
... )
>>> from sqlalchemy.dialects import mysql
>>> print(update_stmt.compile(dialect=mysql.dialect()))
{printsql}UPDATE user_account, address
SET address.email_address=%s, user_account.fullname=%s
WHERE user_account.id = address.user_id AND address.email_address = %s
```

`UPDATE...FROM` can also be combined with the `_sql.Values`{.interpreted-text role="class"} construct on backends such as PostgreSQL, to create a single UPDATE statement that updates multiple rows at once against the named form of VALUES:

``` pycon+sql
>>> from sqlalchemy import Values
>>> values = Values(
...     user_table.c.id,
...     user_table.c.name,
...     name="my_values",
... ).data([(1, "new_name"), (2, "another_name"), ("3", "name_name")])
>>> update_stmt = (
...     user_table.update().values(name=values.c.name).where(user_table.c.id == values.c.id)
... )
>>> from sqlalchemy.dialects import postgresql
>>> print(update_stmt.compile(dialect=postgresql.dialect()))
{printsql}UPDATE user_account
SET name=my_values.name
FROM (VALUES
(%(param_1)s::INTEGER, %(param_2)s::VARCHAR),
(%(param_3)s::INTEGER, %(param_4)s::VARCHAR),
(%(param_5)s::INTEGER, %(param_6)s::VARCHAR)) AS my_values (id, name)
WHERE user_account.id = my_values.id
```

### Parameter Ordered Updates {#tutorial_parameter_ordered_updates}

Another MySQL-only behavior is that the order of parameters in the SET clause of an UPDATE actually impacts the evaluation of each expression. For this use case, the `_sql.Update.ordered_values`{.interpreted-text role="meth"} method accepts a sequence of tuples so that this order may be controlled[^1]:

``` pycon+sql
>>> update_stmt = update(some_table).ordered_values(
...     (some_table.c.y, 20), (some_table.c.x, some_table.c.y + 10)
... )
>>> print(update_stmt)
{printsql}UPDATE some_table SET y=:y, x=(some_table.y + :y_1)
```

## The delete() SQL Expression Construct {#tutorial_deletes}

The `_sql.delete`{.interpreted-text role="func"} function generates a new instance of `_sql.Delete`{.interpreted-text role="class"} which represents a DELETE statement in SQL, that will delete rows from a table.

The `_sql.delete`{.interpreted-text role="func"} statement from an API perspective is very similar to that of the `_sql.update`{.interpreted-text role="func"} construct, traditionally returning no rows but allowing for a RETURNING variant on some database backends.

``` pycon+sql
>>> from sqlalchemy import delete
>>> stmt = delete(user_table).where(user_table.c.name == "patrick")
>>> print(stmt)
{printsql}DELETE FROM user_account WHERE user_account.name = :name_1
```

### Multiple Table Deletes {#tutorial_multi_table_deletes}

Like `_sql.Update`{.interpreted-text role="class"}, `_sql.Delete`{.interpreted-text role="class"} supports the use of correlated subqueries in the WHERE clause as well as backend-specific multiple table syntaxes, such as `DELETE FROM..USING` on MySQL:

``` pycon+sql
>>> delete_stmt = (
...     delete(user_table)
...     .where(user_table.c.id == address_table.c.user_id)
...     .where(address_table.c.email_address == "patrick@aol.com")
... )
>>> from sqlalchemy.dialects import mysql
>>> print(delete_stmt.compile(dialect=mysql.dialect()))
{printsql}DELETE FROM user_account USING user_account, address
WHERE user_account.id = address.user_id AND address.email_address = %s
```

For backends that support a full FROM expression in the `USING` clause, the `_sql.Delete.using`{.interpreted-text role="meth"} method may be used to state that expression explicitly, such as a MySQL joined DELETE:

``` pycon+sql
>>> delete_stmt = (
...     delete(user_table)
...     .using(
...         user_table.outerjoin(
...             address_table,
...             user_table.c.id == address_table.c.user_id,
...         )
...     )
...     .where(address_table.c.email_address == "patrick@aol.com")
... )
>>> print(delete_stmt.compile(dialect=mysql.dialect()))
{printsql}DELETE FROM user_account USING user_account LEFT OUTER JOIN address
ON user_account.id = address.user_id
WHERE address.email_address = %s
```

::: versionadded
2.1 Added `_sql.Delete.using`{.interpreted-text role="meth"} to support explicit USING for DELETE statements on supported backends
:::

## Getting Affected Row Count from UPDATE, DELETE {#tutorial_update_delete_rowcount}

Both `_sql.Update`{.interpreted-text role="class"} and `_sql.Delete`{.interpreted-text role="class"} support the ability to return the number of rows matched after the statement proceeds, for statements that are invoked using Core `_engine.Connection`{.interpreted-text role="class"}, i.e. `_engine.Connection.execute`{.interpreted-text role="meth"}. Per the caveats mentioned below, this value is available from the `_engine.CursorResult.rowcount`{.interpreted-text role="attr"} attribute:

``` pycon+sql
>>> with engine.begin() as conn:
...     result = conn.execute(
...         update(user_table)
...         .values(fullname="Patrick McStar")
...         .where(user_table.c.name == "patrick")
...     )
...     print(result.rowcount)
{execsql}BEGIN (implicit)
UPDATE user_account SET fullname=? WHERE user_account.name = ?
[...] ('Patrick McStar', 'patrick'){stop}
1
{execsql}COMMIT{stop}
```

:::: tip
::: title
Tip
:::

The `_engine.CursorResult`{.interpreted-text role="class"} class is a subclass of `_engine.Result`{.interpreted-text role="class"} which contains additional attributes that are specific to the DBAPI `cursor` object. An instance of this subclass is returned when a statement is invoked via the `_engine.Connection.execute`{.interpreted-text role="meth"} method. When using the ORM, the `_orm.Session.execute`{.interpreted-text role="meth"} method will normally **not** return this type of object, unless the given query uses only Core `.Table`{.interpreted-text role="class"} objects directly.
::::

Facts about `_engine.CursorResult.rowcount`{.interpreted-text role="attr"}:

- The value returned is the number of rows **matched** by the WHERE clause of the statement. It does not matter if the row were actually modified or not.
- `_engine.CursorResult.rowcount`{.interpreted-text role="attr"} is not necessarily available for an UPDATE or DELETE statement that uses RETURNING, or for one that uses an `executemany <tutorial_multiple_parameters>`{.interpreted-text role="ref"} execution. The availability depends on the DBAPI module in use.
- In any case where the DBAPI does not determine the rowcount for some type of statement, the returned value will be `-1`.
- SQLAlchemy pre-memoizes the DBAPIs `cursor.rowcount` value before the cursor is closed, as some DBAPIs don\'t support accessing this attribute after the fact. In order to pre-memoize `cursor.rowcount` for a statement that is not UPDATE or DELETE, such as INSERT or SELECT, the `_engine.Connection.execution_options.preserve_rowcount`{.interpreted-text role="paramref"} execution option may be used.
- Some drivers, particularly third party dialects for non-relational databases, may not support `_engine.CursorResult.rowcount`{.interpreted-text role="attr"} at all. The `_engine.CursorResult.supports_sane_rowcount`{.interpreted-text role="attr"} cursor attribute will indicate this.
- \"rowcount\" is used by the ORM `unit of work`{.interpreted-text role="term"} process to validate that an UPDATE or DELETE statement matched the expected number of rows, and is also essential for the ORM versioning feature documented at `mapper_version_counter`{.interpreted-text role="ref"}.

## Using RETURNING with UPDATE, DELETE

Like the `_sql.Insert`{.interpreted-text role="class"} construct, `_sql.Update`{.interpreted-text role="class"} and `_sql.Delete`{.interpreted-text role="class"} also support the RETURNING clause which is added by using the `_sql.Update.returning`{.interpreted-text role="meth"} and `_sql.Delete.returning`{.interpreted-text role="meth"} methods. When these methods are used on a backend that supports RETURNING, selected columns from all rows that match the WHERE criteria of the statement will be returned in the `_engine.Result`{.interpreted-text role="class"} object as rows that can be iterated:

``` pycon+sql
>>> update_stmt = (
...     update(user_table)
...     .where(user_table.c.name == "patrick")
...     .values(fullname="Patrick the Star")
...     .returning(user_table.c.id, user_table.c.name)
... )
>>> print(update_stmt)
{printsql}UPDATE user_account SET fullname=:fullname
WHERE user_account.name = :name_1
RETURNING user_account.id, user_account.name{stop}

>>> delete_stmt = (
...     delete(user_table)
...     .where(user_table.c.name == "patrick")
...     .returning(user_table.c.id, user_table.c.name)
... )
>>> print(delete_stmt)
{printsql}DELETE FROM user_account
WHERE user_account.name = :name_1
RETURNING user_account.id, user_account.name{stop}
```

## Further Reading for UPDATE, DELETE

::: seealso
API documentation for UPDATE / DELETE:

- `_sql.Update`{.interpreted-text role="class"}
- `_sql.Delete`{.interpreted-text role="class"}

ORM-enabled UPDATE and DELETE:

`orm_expression_update_delete`{.interpreted-text role="ref"} - in the `queryguide_toplevel`{.interpreted-text role="ref"}
:::

[^1]: While Python dictionaries are [guaranteed to be insert ordered](https://mail.python.org/pipermail/python-dev/2017-December/151283.html) as of Python 3.7, the `_sql.Update.ordered_values`{.interpreted-text role="meth"} method still provides an additional measure of clarity of intent when it is essential that the SET clause of a MySQL UPDATE statement proceed in a specific way.
