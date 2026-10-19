::: rst-class
orm-header
:::

# Data Manipulation with the ORM {#tutorial_orm_data_manipulation}

The previous section `tutorial_working_with_data`{.interpreted-text role="ref"} remained focused on the SQL Expression Language from a Core perspective, in order to provide continuity across the major SQL statement constructs. This section will then build out the lifecycle of the `_orm.Session`{.interpreted-text role="class"} and how it interacts with these constructs.

**Prerequisite Sections** - the ORM focused part of the tutorial builds upon two previous ORM-centric sections in this document:

- `tutorial_executing_orm_session`{.interpreted-text role="ref"} - introduces how to make an ORM `_orm.Session`{.interpreted-text role="class"} object
- `tutorial_orm_table_metadata`{.interpreted-text role="ref"} - where we set up our ORM mappings of the `User` and `Address` entities
- `tutorial_selecting_orm_entities`{.interpreted-text role="ref"} - a few examples on how to run SELECT statements for entities like `User`

## Inserting Rows using the ORM Unit of Work pattern {#tutorial_inserting_orm}

When using the ORM, the `_orm.Session`{.interpreted-text role="class"} object is responsible for constructing `_sql.Insert`{.interpreted-text role="class"} constructs and emitting them as INSERT statements within the ongoing transaction. The way we instruct the `_orm.Session`{.interpreted-text role="class"} to do so is by **adding** object entries to it; the `_orm.Session`{.interpreted-text role="class"} then makes sure these new entries will be emitted to the database when they are needed, using a process known as a **flush**. The overall process used by the `_orm.Session`{.interpreted-text role="class"} to persist objects is known as the `unit of work`{.interpreted-text role="term"} pattern.

### Instances of Classes represent Rows

Whereas in the previous example we emitted an INSERT using Python dictionaries to indicate the data we wanted to add, with the ORM we make direct use of the custom Python classes we defined, back at `tutorial_orm_table_metadata`{.interpreted-text role="ref"}. At the class level, the `User` and `Address` classes served as a place to define what the corresponding database tables should look like. These classes also serve as extensible data objects that we use to create and manipulate rows within a transaction as well. Below we will create two `User` objects each representing a potential database row to be INSERTed:

    >>> squidward = User(name="squidward", fullname="Squidward Tentacles")
    >>> krabs = User(name="ehkrabs", fullname="Eugene H. Krabs")

We are able to construct these objects using the names of the mapped columns as keyword arguments in the constructor. This is possible as the `User` class includes an automatically generated `__init__()` constructor that was provided by the ORM mapping so that we could create each object using column names as keys in the constructor.

In a similar manner as in our Core examples of `_sql.Insert`{.interpreted-text role="class"}, we did not include a primary key (i.e. an entry for the `id` column), since we would like to make use of the auto-incrementing primary key feature of the database, SQLite in this case, which the ORM also integrates with. The value of the `id` attribute on the above objects, if we were to view it, displays itself as `None`:

    >>> squidward
    User(id=None, name='squidward', fullname='Squidward Tentacles')

The `None` value is provided by SQLAlchemy to indicate that the attribute has no value as of yet. SQLAlchemy-mapped attributes always return a value in Python and don\'t raise `AttributeError` if they\'re missing, when dealing with a new object that has not had a value assigned.

At the moment, our two objects above are said to be in a state called `transient`{.interpreted-text role="term"} - they are not associated with any database state and are yet to be associated with a `_orm.Session`{.interpreted-text role="class"} object that can generate INSERT statements for them.

### Adding objects to a Session

To illustrate the addition process step by step, we will create a `_orm.Session`{.interpreted-text role="class"} without using a context manager (and hence we must make sure we close it later!):

    >>> session = Session(engine)

The objects are then added to the `_orm.Session`{.interpreted-text role="class"} using the `_orm.Session.add`{.interpreted-text role="meth"} method. When this is called, the objects are in a state known as `pending`{.interpreted-text role="term"} and have not been inserted yet:

    >>> session.add(squidward)
    >>> session.add(krabs)

When we have pending objects, we can see this state by looking at a collection on the `_orm.Session`{.interpreted-text role="class"} called `_orm.Session.new`{.interpreted-text role="attr"}:

    >>> session.new
    IdentitySet([User(id=None, name='squidward', fullname='Squidward Tentacles'), User(id=None, name='ehkrabs', fullname='Eugene H. Krabs')])

The above view is using a collection called `.IdentitySet`{.interpreted-text role="class"} that is essentially a Python set that hashes on object identity in all cases (i.e., using Python built-in `id()` function, rather than the Python `hash()` function).

### Flushing

The `_orm.Session`{.interpreted-text role="class"} makes use of a pattern known as `unit of work`{.interpreted-text role="term"}. This generally means it accumulates changes one at a time, but does not actually communicate them to the database until needed. This allows it to make better decisions about how SQL DML should be emitted in the transaction based on a given set of pending changes. When it does emit SQL to the database to push out the current set of changes, the process is known as a **flush**.

We can illustrate the flush process manually by calling the `_orm.Session.flush`{.interpreted-text role="meth"} method:

``` pycon+sql
>>> session.flush()
{execsql}BEGIN (implicit)
INSERT INTO user_account (name, fullname) VALUES (?, ?) RETURNING id
[... (insertmanyvalues) 1/2 (ordered; batch not supported)] ('squidward', 'Squidward Tentacles')
INSERT INTO user_account (name, fullname) VALUES (?, ?) RETURNING id
[insertmanyvalues 2/2 (ordered; batch not supported)] ('ehkrabs', 'Eugene H. Krabs')
```

Above we observe the `_orm.Session`{.interpreted-text role="class"} was first called upon to emit SQL, so it created a new transaction and emitted the appropriate INSERT statements for the two objects. The transaction now **remains open** until we call any of the `_orm.Session.commit`{.interpreted-text role="meth"}, `_orm.Session.rollback`{.interpreted-text role="meth"}, or `_orm.Session.close`{.interpreted-text role="meth"} methods of `_orm.Session`{.interpreted-text role="class"}.

While `_orm.Session.flush`{.interpreted-text role="meth"} may be used to manually push out pending changes to the current transaction, it is usually unnecessary as the `_orm.Session`{.interpreted-text role="class"} features a behavior known as **autoflush**, which we will illustrate later. It also flushes out changes whenever `_orm.Session.commit`{.interpreted-text role="meth"} is called.

### Autogenerated primary key attributes

Once the rows are inserted, the two Python objects we\'ve created are in a state known as `persistent`{.interpreted-text role="term"}, where they are associated with the `_orm.Session`{.interpreted-text role="class"} object in which they were added or loaded, and feature lots of other behaviors that will be covered later.

Another effect of the INSERT that occurred was that the ORM has retrieved the new primary key identifiers for each new object; internally it normally uses the same `_engine.CursorResult.inserted_primary_key`{.interpreted-text role="attr"} accessor we introduced previously. The `squidward` and `krabs` objects now have these new primary key identifiers associated with them and we can view them by accessing the `id` attribute:

    >>> squidward.id
    4
    >>> krabs.id
    5

:::: tip
::: title
Tip
:::

Why did the ORM emit two separate INSERT statements when it could have used `executemany <tutorial_multiple_parameters>`{.interpreted-text role="ref"}? As we\'ll see in the next section, the `_orm.Session`{.interpreted-text role="class"} when flushing objects always needs to know the primary key of newly inserted objects. If a feature such as SQLite\'s autoincrement is used (other examples include PostgreSQL IDENTITY or SERIAL, using sequences, etc.), the `_engine.CursorResult.inserted_primary_key`{.interpreted-text role="attr"} feature usually requires that each INSERT is emitted one row at a time. If we had provided values for the primary keys ahead of time, the ORM would have been able to optimize the operation better. Some database backends such as `psycopg2 <postgresql_psycopg2>`{.interpreted-text role="ref"} can also INSERT many rows at once while still being able to retrieve the primary key values.
::::

### Getting Objects by Primary Key from the Identity Map

The primary key identity of the objects are significant to the `_orm.Session`{.interpreted-text role="class"}, as the objects are now linked to this identity in memory using a feature known as the `identity map`{.interpreted-text role="term"}. The identity map is an in-memory store that links all objects currently loaded in memory to their primary key identity. We can observe this by retrieving one of the above objects using the `_orm.Session.get`{.interpreted-text role="meth"} method, which will return an entry from the identity map if locally present, otherwise emitting a SELECT:

    >>> some_squidward = session.get(User, 4)
    >>> some_squidward
    User(id=4, name='squidward', fullname='Squidward Tentacles')

The important thing to note about the identity map is that it maintains a **unique instance** of a particular Python object per a particular database identity, within the scope of a particular `_orm.Session`{.interpreted-text role="class"} object. We may observe that the `some_squidward` refers to the **same object** as that of `squidward` previously:

    >>> some_squidward is squidward
    True

The identity map is a critical feature that allows complex sets of objects to be manipulated within a transaction without things getting out of sync.

### Committing

There\'s much more to say about how the `_orm.Session`{.interpreted-text role="class"} works which will be discussed further. For now we will commit the transaction so that we can build up knowledge on how to SELECT rows before examining more ORM behaviors and features:

``` pycon+sql
>>> session.commit()
COMMIT
```

The above operation will commit the transaction that was in progress. The objects which we\'ve dealt with are still `attached`{.interpreted-text role="term"} to the `.Session`{.interpreted-text role="class"}, which is a state they stay in until the `.Session`{.interpreted-text role="class"} is closed (which is introduced at `tutorial_orm_closing`{.interpreted-text role="ref"}).

:::: tip
::: title
Tip
:::

An important thing to note is that attributes on the objects that we just worked with have been `expired`{.interpreted-text role="term"}, meaning, when we next access any attributes on them, the `.Session`{.interpreted-text role="class"} will start a new transaction and re-load their state. This option is sometimes problematic for both performance reasons, or if one wishes to use the objects after closing the `.Session`{.interpreted-text role="class"} (which is known as the `detached`{.interpreted-text role="term"} state), as they will not have any state and will have no `.Session`{.interpreted-text role="class"} with which to load that state, leading to \"detached instance\" errors. The behavior is controllable using a parameter called `.Session.expire_on_commit`{.interpreted-text role="paramref"}. More on this is at `tutorial_orm_closing`{.interpreted-text role="ref"}.
::::

## Updating ORM Objects using the Unit of Work pattern {#tutorial_orm_updating}

In the preceding section `tutorial_core_update_delete`{.interpreted-text role="ref"}, we introduced the `_sql.Update`{.interpreted-text role="class"} construct that represents a SQL UPDATE statement. When using the ORM, there are two ways in which this construct is used. The primary way is that it is emitted automatically as part of the `unit of work`{.interpreted-text role="term"} process used by the `_orm.Session`{.interpreted-text role="class"}, where an UPDATE statement is emitted on a per-primary key basis corresponding to individual objects that have changes on them.

Supposing we loaded the `User` object for the username `sandy` into a transaction (also showing off the `_sql.Select.filter_by`{.interpreted-text role="meth"} method as well as the `_engine.Result.scalar_one`{.interpreted-text role="meth"} method):

``` pycon+sql
>>> sandy = session.execute(select(User).filter_by(name="sandy")).scalar_one()
{execsql}BEGIN (implicit)
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
[...] ('sandy',)
```

The Python object `sandy` as mentioned before acts as a **proxy** for the row in the database, more specifically the database row **in terms of the current transaction**, that has the primary key identity of `2`:

    >>> sandy
    User(id=2, name='sandy', fullname='Sandy Cheeks')

If we alter the attributes of this object, the `_orm.Session`{.interpreted-text role="class"} tracks this change:

    >>> sandy.fullname = "Sandy Squirrel"

The object appears in a collection called `_orm.Session.dirty`{.interpreted-text role="attr"}, indicating the object is \"dirty\":

    >>> sandy in session.dirty
    True

When the `_orm.Session`{.interpreted-text role="class"} next emits a flush, an UPDATE will be emitted that updates this value in the database. As mentioned previously, a flush occurs automatically before we emit any SELECT, using a behavior known as **autoflush**. We can query directly for the `User.fullname` column from this row and we will get our updated value back:

``` pycon+sql
>>> sandy_fullname = session.execute(select(User.fullname).where(User.id == 2)).scalar_one()
{execsql}UPDATE user_account SET fullname=? WHERE user_account.id = ?
[...] ('Sandy Squirrel', 2)
SELECT user_account.fullname
FROM user_account
WHERE user_account.id = ?
[...] (2,){stop}
>>> print(sandy_fullname)
Sandy Squirrel
```

We can see above that we requested that the `_orm.Session`{.interpreted-text role="class"} execute a single `_sql.select`{.interpreted-text role="func"} statement. However the SQL emitted shows that an UPDATE were emitted as well, which was the flush process pushing out pending changes. The `sandy` Python object is now no longer considered dirty:

    >>> sandy in session.dirty
    False

However note we are **still in a transaction** and our changes have not been pushed to the database\'s permanent storage. Since Sandy\'s last name is in fact \"Cheeks\" not \"Squirrel\", we will repair this mistake later when we roll back the transaction. But first we\'ll make some more data changes.

::: seealso
`session_flushing`{.interpreted-text role="ref"}- details the flush process as well as information about the `_orm.Session.autoflush`{.interpreted-text role="paramref"} setting.
:::

## Deleting ORM Objects using the Unit of Work pattern {#tutorial_orm_deleting}

To round out the basic persistence operations, an individual ORM object may be marked for deletion within the `unit of work`{.interpreted-text role="term"} process by using the `_orm.Session.delete`{.interpreted-text role="meth"} method. Let\'s load up `patrick` from the database:

``` pycon+sql
>>> patrick = session.get(User, 3)
{execsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.id = ?
[...] (3,)
```

If we mark `patrick` for deletion, as is the case with other operations, nothing actually happens yet until a flush proceeds:

    >>> session.delete(patrick)

Current ORM behavior is that `patrick` stays in the `_orm.Session`{.interpreted-text role="class"} until the flush proceeds, which as mentioned before occurs if we emit a query:

``` pycon+sql
>>> session.execute(select(User).where(User.name == "patrick")).first()
{execsql}SELECT address.id, address.email_address, address.user_id
FROM address
WHERE ? = address.user_id
[...] (3,)
DELETE FROM user_account WHERE user_account.id = ?
[...] (3,)
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
[...] ('patrick',)
```

Above, the SELECT we asked to emit was preceded by a DELETE, which indicated the pending deletion for `patrick` proceeded. There was also a `SELECT` against the `address` table, which was prompted by the ORM looking for rows in this table which may be related to the target row; this behavior is part of a behavior known as `cascade`{.interpreted-text role="term"}, and can be tailored to work more efficiently by allowing the database to handle related rows in `address` automatically; the section `cascade_delete`{.interpreted-text role="ref"} has all the detail on this.

::: seealso
`cascade_delete`{.interpreted-text role="ref"} - describes how to tune the behavior of `_orm.Session.delete`{.interpreted-text role="meth"} in terms of how related rows in other tables should be handled.
:::

Beyond that, the `patrick` object instance now being deleted is no longer considered to be persistent within the `_orm.Session`{.interpreted-text role="class"}, as is shown by the containment check:

    >>> patrick in session
    False

However just like the UPDATEs we made to the `sandy` object, every change we\'ve made here is local to an ongoing transaction, which won\'t become permanent if we don\'t commit it. As rolling the transaction back is actually more interesting at the moment, we will do that in the next section.

## Bulk / Multi Row INSERT, upsert, UPDATE and DELETE {#tutorial_orm_bulk}

The `unit of work`{.interpreted-text role="term"} techniques discussed in this section are intended to integrate `dml`{.interpreted-text role="term"}, or INSERT/UPDATE/DELETE statements, with Python object mechanics, often involving complex graphs of inter-related objects. Once objects are added to a `.Session`{.interpreted-text role="class"} using `.Session.add`{.interpreted-text role="meth"}, the unit of work process transparently emits INSERT/UPDATE/DELETE on our behalf as attributes on our objects are created and modified.

However, the ORM `.Session`{.interpreted-text role="class"} also has the ability to process commands that allow it to emit INSERT, UPDATE and DELETE statements directly without being passed any ORM-persisted objects, instead being passed lists of values to be INSERTed, UPDATEd, or upserted, or WHERE criteria so that an UPDATE or DELETE statement that matches many rows at once can be invoked. This mode of use is of particular importance when large numbers of rows must be affected without the need to construct and manipulate mapped objects, which may be cumbersome and unnecessary for simplistic, performance-intensive tasks such as large bulk inserts.

The Bulk / Multi row features of the ORM `_orm.Session`{.interpreted-text role="class"} make use of the `_dml.insert`{.interpreted-text role="func"}, `_dml.update`{.interpreted-text role="func"} and `_dml.delete`{.interpreted-text role="func"} constructs directly, and their usage resembles how they are used with SQLAlchemy Core (first introduced in this tutorial at `tutorial_core_insert`{.interpreted-text role="ref"} and `tutorial_core_update_delete`{.interpreted-text role="ref"}). When using these constructs with the ORM `_orm.Session`{.interpreted-text role="class"} instead of a plain `_engine.Connection`{.interpreted-text role="class"}, their construction, execution and result handling is fully integrated with the ORM.

For background and examples on using these features, see the section `orm_expression_update_delete`{.interpreted-text role="ref"} in the `queryguide_toplevel`{.interpreted-text role="ref"}.

::: seealso
`orm_expression_update_delete`{.interpreted-text role="ref"} - in the `queryguide_toplevel`{.interpreted-text role="ref"}
:::

## Rolling Back

The `_orm.Session`{.interpreted-text role="class"} has a `_orm.Session.rollback`{.interpreted-text role="meth"} method that as expected emits a ROLLBACK on the SQL connection in progress. However, it also has an effect on the objects that are currently associated with the `_orm.Session`{.interpreted-text role="class"}, in our previous example the Python object `sandy`. While we changed the `.fullname` of the `sandy` object to read `"Sandy Squirrel"`, we want to roll back this change. Calling `_orm.Session.rollback`{.interpreted-text role="meth"} will not only roll back the transaction but also **expire** all objects currently associated with this `_orm.Session`{.interpreted-text role="class"}, which will have the effect that they will refresh themselves when next accessed using a process known as `lazy loading`{.interpreted-text role="term"}:

``` pycon+sql
>>> session.rollback()
ROLLBACK
```

To view the \"expiration\" process more closely, we may observe that the Python object `sandy` has no state left within its Python `__dict__`, with the exception of a special SQLAlchemy internal state object:

    >>> sandy.__dict__
    {'_sa_instance_state': <sqlalchemy.orm.state.InstanceState object at 0x...>}

This is the \"`expired`{.interpreted-text role="term"}\" state; accessing the attribute again will autobegin a new transaction and refresh `sandy` with the current database row:

``` pycon+sql
>>> sandy.fullname
{execsql}BEGIN (implicit)
SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.id = ?
[...] (2,){stop}
'Sandy Cheeks'
```

We may now observe that the full database row was also populated into the `__dict__` of the `sandy` object:

    >>> sandy.__dict__  # doctest: +SKIP
    {'_sa_instance_state': <sqlalchemy.orm.state.InstanceState object at 0x...>,
     'id': 2, 'name': 'sandy', 'fullname': 'Sandy Cheeks'}

For deleted objects, when we earlier noted that `patrick` was no longer in the session, that object\'s identity is also restored:

    >>> patrick in session
    True

and of course the database data is present again as well:

``` pycon+sql
>>> session.execute(select(User).where(User.name == "patrick")).scalar_one() is patrick
{execsql}SELECT user_account.id, user_account.name, user_account.fullname
FROM user_account
WHERE user_account.name = ?
[...] ('patrick',){stop}
True
```

## Closing a Session {#tutorial_orm_closing}

Within the above sections we used a `_orm.Session`{.interpreted-text role="class"} object outside of a Python context manager, that is, we didn\'t use the `with` statement. That\'s fine, however if we are doing things this way, it\'s best that we explicitly close out the `_orm.Session`{.interpreted-text role="class"} when we are done with it:

``` pycon+sql
>>> session.close()
{execsql}ROLLBACK
```

Closing the `_orm.Session`{.interpreted-text role="class"}, which is what happens when we use it in a context manager as well, accomplishes the following things:

- It `releases`{.interpreted-text role="term"} all connection resources to the connection pool, cancelling out (e.g. rolling back) any transactions that were in progress.

  This means that when we make use of a session to perform some read-only tasks and then close it, we don\'t need to explicitly call upon `_orm.Session.rollback`{.interpreted-text role="meth"} to make sure the transaction is rolled back; the connection pool handles this.

- It **expunges** all objects from the `_orm.Session`{.interpreted-text role="class"}.

  This means that all the Python objects we had loaded for this `_orm.Session`{.interpreted-text role="class"}, like `sandy`, `patrick` and `squidward`, are now in a state known as `detached`{.interpreted-text role="term"}. In particular, we will note that objects that were still in an `expired`{.interpreted-text role="term"} state, for example due to the call to `_orm.Session.commit`{.interpreted-text role="meth"}, are now non-functional, as they don\'t contain the state of a current row and are no longer associated with any database transaction in which to be refreshed:

      # note that 'squidward.name' was just expired previously, so its value is unloaded
      >>> squidward.name
      Traceback (most recent call last):
        ...
      sqlalchemy.orm.exc.DetachedInstanceError: Instance <User at 0x...> is not bound to a Session; attribute refresh operation cannot proceed

  The detached objects can be re-associated with the same, or a new `_orm.Session`{.interpreted-text role="class"} using the `_orm.Session.add`{.interpreted-text role="meth"} method, which will re-establish their relationship with their particular database row:

  ``` pycon+sql
  >>> session.add(squidward)
  >>> squidward.name
  {execsql}BEGIN (implicit)
  SELECT user_account.id, user_account.name, user_account.fullname
  FROM user_account
  WHERE user_account.id = ?
  [...] (4,){stop}
  'squidward'
  ```

  :::: tip
  ::: title
  Tip
  :::

  Try to avoid using objects in their detached state, if possible. When the `_orm.Session`{.interpreted-text role="class"} is closed, clean up references to all the previously attached objects as well. For cases where detached objects are necessary, typically the immediate display of just-committed objects for a web application where the `_orm.Session`{.interpreted-text role="class"} is closed before the view is rendered, set the `_orm.Session.expire_on_commit`{.interpreted-text role="paramref"} flag to `False`.
  ::::
