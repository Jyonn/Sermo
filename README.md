# Sermo Chat System API Documentation

This documentation covers the API endpoints, their functionality (views), the data models, and the response format for a Django-based chat system. All API responses are returned in JSON format for consistency.

## Endpoints

Below is a list of all available API endpoints with their HTTP methods and a brief description:

- **User Authentication & Status**
  - POST /users/host – **Host Login/Register.** Authenticate a host user (or create a new host account if it doesn’t exist) using a name and password.
  - POST /users/guest – **Guest Login/Register.** Authenticate a guest user (or create a new guest account) under a given host using name, password, and host ID.
  - GET /users/heartbeat – **User Heartbeat.** Keeps the authenticated user marked as online (updates last active timestamp).
- **Chat Management**
  - GET /chats/ – **List Chats.** Retrieve all chats (both single and group chats) that the authenticated user is involved in.
  - POST /chats/group – **Create Group Chat.** (Host only) Create a new group chat with multiple guests.
  - DELETE /chats/group – **Delete Group Chat.** (Host only) Delete an existing group chat (the host must be the owner of that chat).
  - POST /chats/group/name – **Rename Group Chat.** (Host only) Change the name of an existing group chat.
- **Messaging**
  - GET /messages/ – **List Messages.** Retrieve messages in a specified chat, with optional pagination parameters (before/after a certain message).
  - POST /messages/ – **Send Message.** Post a new message to a specified chat.
  - DELETE /messages/ – **Delete Message.** Remove a specific message (only the message sender or the chat’s host can delete it).

## Views and Endpoint Details

Below are details of each API view (endpoint), including functionality, required inputs, and outputs. All endpoints (except login) require an Authorization header with a valid JWT token obtained from the login endpoints.

### HostLoginView – POST /users/host

**Functionality:** Authenticates a host user. If the host account with the given name does not exist, a new host account is created. Returns a JWT authentication token and basic user info.

**Request:** JSON body with the following fields:

- `name` (string): Host name (required).
- `password` (string): Host password (required).

**Response:** Returns a JSON object containing the token and user details. The response body includes:

- `auth` (string): A JWT token string to use in the Authorization header for subsequent requests.
- `data` (dict): An object with the host user’s information:
  - `user_id` (int): The unique ID of the user.
  - `name` (string): The user’s name.
  - `is_alive` (bool): A flag indicating if the user is online.
  - `guest` (bool): A flag indicating if the user is a guest (always `false` for hosts).

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": {
    "auth": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...<token>...",
    "data": {
      "user_id": 1,
      "name": "Alice",
      "is_alive": true,
      "guest": false
    }
  },
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

### GuestLoginView – POST /users/guest

**Functionality:** Authenticates a guest user under a specific host. If the guest account doesn’t exist, it will be created and associated with the given host. Also ensures a one-on-one chat is set up between the guest and host. Returns a JWT token and user info.

**Request:** JSON body with the following fields:

- `name` (string): Guest name (required).
- `password` (string): Guest password (optional).
- `host_id` (int): Host ID under which the guest should be registered (required).

**Response:** Returns a JWT token and the guest user’s details. The format is the same as for host login, with guest flag set to `true`.

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": {
    "auth": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...<token>...",
    "data": {
      "user_id": 2,
      "name": "Bob",
      "is_alive": true,
      "guest": true
    }
  },
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

**Note:** The auth token from either login endpoint should be included in the Authorization header (as a Bearer token) for all subsequent API requests.

### HeartbeatView – GET /users/heartbeat

**Functionality:** Keeps the currently authenticated user marked as online. Updates the user’s last heartbeat timestamp to indicate they are active. This endpoint requires a valid Authorization token and works for both host and guest users.

**Request:** No request body is required. Just send a GET request with the `Authorization` header (JWT token).

**Response:** Returns a success status with no additional data in the body. This confirms the heartbeat was received.

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": {},
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

### ChatListView – GET /chats/

**Functionality:** Retrieves the list of all chats the authenticated user is part of. This includes one-on-one chats (between a host and a single guest) and group chats. Host users will see all their chats with guests (including one-on-one chats with each guest and any group chats they have created). Guest users will see their one-on-one chat with the host and any group chats the host has added them to.

**Request:** No body or query parameters (aside from the required auth token). Simply a `GET` request to /chats/.

**Response:** The body will contain an array of chat objects. Each chat object has the following structure:

- `chat_id` (int): The unique ID of the chat.
- `host` (dict): An object with the host’s basic info (`name`, `user_id`, `is_alive`, `guest:false`).
- `guests` (list): _For group chats:_ an array of guest user objects (each with `name`, `user_id`, `is_alive`, `guest:true`) who are participants. (This field is present only for group chats.)
- `guest` (dict): _For one-on-one chats:_ a guest user object (with `name`, `user_id`, etc.) who is the participant. (This field is present only for one-on-one chats.)
- `name` (string): _For group chats:_ the name of the group chat. (Not present for one-on-one chats.)
- `created_at` (float): Timestamp (Unix epoch in seconds) when the chat was created.
- `last_chat_at` (float): Timestamp of the last message sent in the chat.
- `group` (bool): A flag indicating if the chat is a group chat (`true`) or a one-on-one chat (`false`).

**Example Response:** (a host user with one one-on-one chat and one group chat)

```json
{
  "code": 200,
  "message": "OK",
  "body": [
    {
      "chat_id": 10,
      "host": {
        "name": "Alice",
        "user_id": 1,
        "is_alive": true,
        "guest": false
      },
      "guest": {
        "name": "Bob",
        "user_id": 2,
        "is_alive": true,
        "guest": true
      },
      "created_at": 1680000000,
      "last_chat_at": 1680003600,
      "group": false
    },
    {
      "chat_id": 12,
      "host": {
        "name": "Alice",
        "user_id": 1,
        "is_alive": true,
        "guest": false
      },
      "guests": [
        {
          "name": "Bob",
          "user_id": 2,
          "is_alive": true,
          "guest": true
        },
        {
          "name": "Carol",
          "user_id": 3,
          "is_alive": false,
          "guest": true
        }
      ],
      "name": "Group Chat (3)",
      "created_at": 1680001000,
      "last_chat_at": 1680002000,
      "group": true
    }
  ],
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

*(In the above example, “Group Chat (3)” indicates a group chat with 3 members total: 1 host + 2 guests. The timestamps are example Unix timestamps in seconds.)*

### GroupChatView – POST /chats/group and DELETE /chats/group

This view handles creating and deleting group chats. **Only host users** (authenticated as hosts) can use these endpoints.

#### POST /chats/group – Create a Group Chat

**Functionality:** Creates a new group chat with the host as owner and multiple guests as members. The host must supply a list of guest user IDs to include. The group chat will be initialized and a default name is assigned (e.g., “Group Chat (N)” where N is total members).

**Request:** JSON body with the following fields:

- `guests` (list of int): A list of guest user IDs to include in the group. There must be at least 2 guests (if only one guest is provided, the server will reject it since that should be a one-on-one chat). All listed guests must belong to the authenticated host’s account.

**Response:** On success, returns the newly created group chat object (same format as described in **ChatListView**, with `group: true`). This includes the `chat_id` of the new chat, the host info, the list of guests info, the generated group name, and timestamps.

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": {
    "chat_id": 15,
    "host": {
      "name": "Alice",
      "user_id": 1,
      "is_alive": true,
      "guest": false
    },
    "guests": [
      {
        "name": "Bob",
        "user_id": 2,
        "is_alive": true,
        "guest": true
      },
      {
        "name": "Carol",
        "user_id": 3,
        "is_alive": false,
        "guest": true
      }
    ],
    "name": "Group Chat (3)",
    "created_at": 1680100000,
    "last_chat_at": 1680100000,
    "group": true
  },
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

#### DELETE /chats/group – Delete a Group Chat

**Functionality:** Deletes an existing group chat. The authenticated host must be the owner of the group chat to delete it. This will mark the chat as deleted so it no longer appears in lists.

**Request:** Provide the target group chat’s ID as a query parameter: `chat_id`. For example, `DELETE /chats/group?chat_id=15`. (No JSON body.)

**Response:** Returns a confirmation with no content in the body (just an “OK” status).

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": {},
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

### GroupChatNameView – POST /chats/group/name

**Functionality:** Renames an existing group chat. Only the host (chat owner) can rename the group chat. This will update the group’s name attribute.

**Request:** 

Query parameter: `chat_id` – The ID of the group chat to rename. (Example: `POST /chats/group/name?chat_id=15`)
JSON body with the following field:

- `name` (string): The new name to assign to the group chat. (Required, up to 20 characters)

**Response:** Returns the updated group chat object with the new name. The structure is the same as a chat object described earlier (host, guests, etc.), with the name field reflecting the change.

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": {
    "chat_id": 15,
    "host": {
      "name": "Alice",
      "user_id": 1,
      "is_alive": true,
      "guest": false
    },
    "guests": [
      {
        "name": "Bob",
        "user_id": 2,
        "is_alive": true,
        "guest": true
      },
      {
        "name": "Carol",
        "user_id": 3,
        "is_alive": false,
        "guest": true
      }
    ],
    "name": "Project Team Chat",
    "created_at": 1680100000,
    "last_chat_at": 1680200000,
    "group": true
  },
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```
_(In this example, the group chat 15 has been renamed to “Project Team Chat”.)_

### MessageListView – GET /messages/, POST /messages/, DELETE /messages/

This view handles retrieving, sending, and deleting messages within chats. The user (host or guest) must be a participant in the chat to interact with its messages. All responses for message actions will include message data in a standardized format.

#### GET /messages/ – Retrieve Messages

**Functionality:** Fetches messages from a specific chat, with optional filters for pagination (before or after a given message). The messages are returned in chronological order depending on the filter: latest messages, older messages, or newer messages.

**Request:** Query parameters:

- `chat_id` (int): The ID of the chat to retrieve messages from (required).
- `limit` (int): The maximum number of messages to return. Must be between 5 and 100. If not provided, a default may be used (e.g., 5) (optional).
- `before` (int): If provided, retrieve messages older than the message with this ID. This is typically used for paginating backwards (e.g., loading previous messages). The returned list will contain up to limit messages with IDs less than the before value, ordered from newest to oldest. (optional).
- `after` (int): If provided, retrieve messages newer than the message with this ID. This can be used to load recent messages after a certain point (for example, polling for new messages). The returned list will contain up to limit messages with IDs greater than the `after` value, ordered from oldest to newest. (optional).

- _Note:_ `before` and `after` are mutually exclusive – use one or the other (or neither) in a request. If neither is given, the endpoint returns the latest `limit` messages in the chat (most recent messages).

**Response:** The body will be a list of message objects. Each message object includes:

- `message_id` (int): The unique ID of the message.
- `user` (dict): An object with the sender’s basic info (`name`, `user_id`, `is_alive`, `guest` flag). This identifies who sent the message (could be a host or guest user).
- `type` (int): An integer indicating the message type. (Possible values: `0` = Text, `1` = Image, `2` = File, `3` = System message.)
- `content` (string): The content of the message. For a text message (type=`0`), this is the text string. For other types, this might be a URL or identifier of the image/file, or text for system messages.
- `created_at` (float): Timestamp (Unix epoch in seconds) when the message was created/sent.

Messages are returned according to the specified filter: for example, if before is used, the list is comprised of messages older than the given message ID. If after is used, the list contains messages newer than that ID. Without filters, it returns the latest messages up to the `limit`.

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": [
    {
      "message_id": 45,
      "user": {
        "name": "Bob",
        "user_id": 2,
        "is_alive": true,
        "guest": true
      },
      "type": 0,
      "content": "Hello, this is a message",
      "created_at": 1680003000
    },
    {
      "message_id": 46,
      "user": {
        "name": "Alice",
        "user_id": 1,
        "is_alive": true,
        "guest": false
      },
      "type": 0,
      "content": "Hi Bob, got it!",
      "created_at": 1680003050
    }
  ],
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

_(In this example, two text messages are returned: message 45 sent by Bob (a guest) and message 46 by Alice (the host).)_

#### POST /messages/ – Send a New Message

**Functionality:** Sends a new message to a specified chat. The message will be recorded and broadcast to the chat participants. The user sending the message must be part of the chat (either the host or one of the guests in that chat).

**Request:**

- Query parameter: `chat_id` (int, required) – The ID of the chat to send the message to.
- JSON body:
    - `content` (string): The message content (string). The interpretation of this field depends on the type. For text messages, this is the text. For image or file messages, this might be a URL or path, etc (required).
    - `type` (int): The message type (int code as described above: 0=Text, 1=Image, 2=File, 3=System) (required).

**Response:** Returns the newly created message object in the response body (with the same fields as described in Get Messages). This confirms that the message was sent successfully.

**Example Response:**

```json
{
  "code": 200,
  "message": "OK",
  "body": {
    "message_id": 47,
    "user": {
      "name": "Bob",
      "user_id": 2,
      "is_alive": true,
      "guest": true
    },
    "type": 0,
    "content": "This is a new message",
    "created_at": 1680004000
  },
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

#### DELETE /messages/ – Delete a Message

**Functionality:** Deletes a specific message from a chat. Only certain users are allowed to delete a message: the user who originally sent the message, or the host user who owns the chat (acting as a moderator). If a guest tries to delete another user’s message (not their own) and they are not the host, the operation will be forbidden.

**Request:** Query parameter: `message_id` (int, required) – The ID of the message to delete. For example, `DELETE /messages/?message_id=47`. No request body is needed.

**Response:** Returns a success confirmation with no content in the body. If the user is not authorized to delete the message (not the owner or host), an error will be returned (with an error code and message). On success, the response will look like:

**Example Response:**
```json
{
  "code": 200,
  "message": "OK",
  "body": null,
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

## Models

Below are the Django models that define the data structure of the chat system. Each model’s attributes and relationships are described. These models correspond to the data returned by the API.

### User Models

**BaseUser** – _Abstract base model for users._

This model isn’t directly exposed via endpoints but provides common fields for the specific user types (Host and Guest). Key fields include:

- `id` (AutoField): The unique ID of the user (referred to as `user_id` in API responses).
- `role` (IntegerField): An integer representing the user role (`0`=Host, `1`=Guest).
- `offline_notification_interval` (PositiveIntegerField): Interval (in minutes) for offline notification checks (default defined by system, e.g., minimum interval).
- `notification_channel` (IntegerField): Preferred notification channel (if any) for offline notifications. `0` = Unset, `1` = Email, `2` = SMS, `3` = Bark (push notification service).
- `last_heartbeat` (DateTimeField): Timestamp of the last heartbeat received from the user. Used to compute `is_alive` status in API (online if last heartbeat is within the allowed interval).
- `email`, `phone`, `barks` (CharField): Contact details for the user, corresponding to possible notification channels (email address, phone number, or Bark endpoint) (optional).
- `created_at` (DateTimeField): Timestamp of user account creation.
- `salt` (CharField): Random salt value for password hashing.
- `password` (CharField): Hashed password value.

_Relationships_: `BaseUser` is extended by `HostUser` and `GuestUser`. It also is referenced by other models: for example, the Message model has a foreign key to `BaseUser` (meaning messages store a reference to the user who sent them, which can be either a `HostUser` or `GuestUser`).

**HostUser** – _Represents a host (primary user) in the system._

Fields specific to `HostUser` (inherits all `BaseUser` fields, with `role = 0`):

- `name` (CharField): The unique username for the host. (Unique across all hosts.)
- `lower_name` (CharField): Lowercase version of the name for case-insensitive lookups.
- `password` (CharField): Inherited from `BaseUser` (hashed password). For hosts, this is required and set on creation.

_Relationships_: A HostUser can have many associated GuestUser accounts. There is a one-to-many relationship: each GuestUser has a foreign key host pointing to HostUser. In the HostUser model, the reverse relationship allows accessing all guests. HostUser is also linked to chats:

- As the **owner** (host field) of every chat (both `SingleChat` and `GroupChat`) that they participate in.
- As the **sender** of messages in a chat (if the host posts a message, the Message’s `user` field will reference the HostUser).

**GuestUser** – _Represents a guest user associated with a host._

Fields specific to `GuestUser` (inherits all `BaseUser` fields, with `role = 1`):

- `name` (CharField): The unique username for the guest (unique per host, but not globally unique across the system).
- `lower_name` (CharField): Lowercase version of the name for case-insensitive lookups.
- `password` (CharField): Inherited from `BaseUser` (hashed password). For guests, this is optional (guests can be created without a password).
- `host` (ForeignKey): The host account that this guest is associated with. This is a required link; each GuestUser belongs to exactly one HostUser.

_Relationships:_ Each GuestUser is linked to one HostUser (their `host`). Guests participate in chats:

- In a one-on-one chat (SingleChat) with their host (each guest typically has exactly one SingleChat with their host, created on first login).
- Potentially in none or multiple GroupChats that their host created. A guest can be a member of many group chats, all owned by their same host (the system enforces that all members of a group chat have the same host).
- As senders of messages (if a guest sends a message, the Message’s user will reference the GuestUser).

### Chat Models

Chats connect hosts and guests. There are two types of chats: single chats (one host and one guest) and group chats (one host and multiple guests). Both types share some common fields defined in a base model.

**BaseChat** – _Abstract base model for chats._

Contains fields common to any chat session:

- `id` (AutoField): The unique ID of the chat (referred to as `chat_id` in API responses).
- `host` (ForeignKey): The host user who owns the chat. Each chat has exactly one host.
- `scheme` (IntegerField): An integer representing the chat scheme (`0`=Single, `1`=Group).
- `created_at` (DateTimeField): Timestamp of chat creation.
- `last_chat_at` (DateTimeField): Timestamp of the last message sent in the chat.
- `is_deleted` (BooleanField): Flag indicating if the chat has been deleted. Deleted chats are typically excluded from results (the chat list API does not show chats where `is_deleted = true`). When a chat is “deleted” via the API, this flag is set.

_Relationships:_ BaseChat is extended by the actual chat types (SingleChat and GroupChat). Other models: Message has a foreign key to BaseChat (each message belongs to one chat).

**SingleChat** – _Represents a one-on-one chat between a host and a guest._

This model inherits BaseChat (so it includes id, host, etc., with `scheme = 0`) and adds:

- `guest` (ForeignKey): The guest who is the other participant in this single chat. There is exactly one guest per single chat. This field is unique and indexed, ensuring a guest cannot appear in two separate single chats (with the same host). In practice, each GuestUser will have at most one SingleChat with their host.

_Relationships:_ A SingleChat links one HostUser (as `host`) with one GuestUser (as `guest`). By design, a HostUser can have many SingleChat entries (one per guest), but each GuestUser–HostUser pair has at most one SingleChat. The SingleChat is used to facilitate direct messaging between that guest and host. Messages in a single chat will have their chat field referencing a SingleChat, and the user field of each message will be either the host or the guest of that chat.

**GroupChat** – _Represents a group chat with multiple guests._

This model inherits BaseChat (with `scheme = 1`) and adds:

- `name` (CharField): The name of the group chat. This can be set or changed by the host. By default, a new group chat might be named “Group Chat (N)” where N is the number of members, but the host can rename it via the API.
- `guests` (ManyToManyField): The set of guest users who are members of this group chat. There can be two or more guests in a group chat (a minimum of 2 guests is required, otherwise the chat should be a SingleChat). All guests in a given GroupChat must belong to the same host (the host who created the chat). This is ensured by the application logic (the host cannot mix guests from different accounts in one chat).

_Relationships:_ A GroupChat has one HostUser (owner/creator) and can have multiple GuestUser members. A HostUser can create multiple GroupChats, and each guest can be in multiple group chats (all created by their host). Messages in group chats will reference the GroupChat via the chat foreign key, and user for each message will be one of the host or guests in that group.

### Message Model

**Message** – _Represents a message sent in a chat._

Fields:

- `id` (AutoField): The unique ID of the message (referred to as `message_id` in API responses).
- `chat` (ForeignKey): The chat that this message belongs to. Each message is part of exactly one chat (either a SingleChat or GroupChat).
- `user` (ForeignKey): The user who sent the message. This can be a HostUser or a GuestUser (the BaseUser reference will point to the correct subclass). It indicates the sender of the message.
- `type` (IntegerField): An integer representing the message type (`0`=Text, `1`=Image, `2`=File, `3`=System message). These types determine how the content field is to be interpreted (e.g., if type is Image or File, the content might be a URL or file identifier rather than plain text).
- `content` (TextField): The content of the message. This can be text, a URL, or other data depending on the message type.
- `created_at` (DateTimeField): Timestamp of message creation.
- `is_deleted` (BooleanField): Flag indicating if the message has been deleted. Deleted messages are typically excluded from results (the message list API does not show messages where `is_deleted = true`). When a message is “deleted” via the API, this flag is set.

_Relationships:_ Each Message is linked to one chat (`chat`) and one sender user (`user`). The chat could be either type of chat, and the `user` could be host or guest. In terms of foreign keys:

- `chat_id` in the Message corresponds to a `BaseChat` entry (which can be a SingleChat or GroupChat).
- `user_id` in the Message corresponds to a `BaseUser` entry (which can be a HostUser or GuestUser).

When retrieving messages via the API, the user field in the JSON response is given in a simplified form (`tiny_json`) containing the user’s id, name, and status, rather than the full user model.

### Additional Model Behaviors

- **User Model behaviors:**
  - HostUser and GuestUser have custom class methods `login` to handle authentication (creating the user if not exists, and verifying password). These are used by the login endpoints.
  - Both HostUser and GuestUser implement `jwt_json()` which returns the `tiny_json` (basic info) used to encode JWT tokens, and `heartbeat()` which updates their `last_heartbeat`.
  - `tiny_json` for any user yields a dictionary: `{ "user_id": ..., "name": "...", "is_alive": ..., "guest": <bool> }`. This format is used in all responses to represent users briefly. `is_alive` is determined by checking if the last heartbeat is within the allowed offline interval.
- **Chat Model behaviors:**
  - SingleChat has a unique constraint on its guest field, ensuring one active single chat per guest. The `get_or_create(guest)` class method either finds the existing single chat for that guest or creates a new one (used during guest login to ensure a chat exists).
  - GroupChat’s `create(host, guests)` class method handles validation: it ensures the guests list is not empty or too small (must be at least 2 guests), and that all guests belong to the same host. It then creates a GroupChat with the given members and auto-generates a name like “Group Chat (N)”.
  - GroupChat has methods `rename(name)`, `add_guest(guest)`, and `remove_guest(guest)` for managing the group membership and name. (The API currently exposes rename via an endpoint; adding/removing guests might not have direct endpoints in this version.)
  - Both SingleChat and GroupChat implement `json()` and `jsonl()` to output their data. The `json()` method returns the detailed JSON (as seen in responses), while `jsonl()` is often the same or a subset for list contexts. Fields like `id` are mapped to `chat_id` in these JSON outputs, and they include either a single guest or a list of guests depending on chat type, plus the host and timestamps.
- **Message Model behaviors:**
  - The `Message.create(chat, user, message_type, content)` class method handles creating a message. It validates that the user is allowed in that chat: the user must be the host of the chat or one of the guests in the chat. If not, it throws a “Not a member” error. If valid, it saves the new Message.
  - The Message model defines custom query methods for fetching messages relative to a given message ID:
    - `Message.latest(chat, limit)` – fetches the latest limit messages in the chat (sorted by created_at descending).
    - `Message.older(chat, message_id, limit)` – fetches up to limit messages with IDs less than the given `message_id` (i.e., messages sent before that message), sorted by created_at descending (newest first among those older than the reference).
    - `Message.newer(chat, message_id, limit)` – fetches up to limit messages with IDs greater than the given message_id (messages sent after that message), sorted by created_at ascending (so oldest first among those newer than the reference, ensuring chronological order forward from the reference point).
  - The `jsonl()` method for Message outputs the message data in a dictionary form used by the API, mapping `id` to `message_id` and including the `user` (tiny profile), `type`, `content`, and `created_at` (as a timestamp).
  - The `remove()` method (called when deleting a message) simply sets `is_deleted = True` for that message. Deleted messages will not appear in future fetches.

## Error Handling

The system defines various error conditions (such as trying to create a group chat with no guests, or providing wrong credentials, etc.). While not a separate model, it’s useful to know that error responses will also follow a standard JSON format (see below), with an appropriate HTTP status code and a descriptive message.

For example, if a login fails due to incorrect password, the response might be:

```json
{
  "code": 400,
  "message": "Incorrect password",
  "body": null,
  "details": [],
  "user_message": "Incorrect password",
  "identifier": "USER_ERRORS@PASSWORD_ERROR"
}
```

Each error has an `identifier` that combines a category and error code, and a human-readable message (and `user_message`, which might be a localized or same message). The body will be `null` in error cases.

## Response Format

All API responses are in JSON and share a common envelope structure for consistency. Whether the request is successful or an error occurs, the response JSON will include the following top-level fields:

- `code` (int): The HTTP status code of the response (e.g., `200` for success, `400` for bad request, etc.).
- `message` (string):  A brief message about the result. For successful operations this is usually `"OK"`. For errors, this will contain an error message describing what went wrong (e.g., `"Token expired"`, `"Chat 15 does not exist"`, etc.).
- `user_message` (string): A message intended for end-user display. In many cases this will mirror message, but it could be a more user-friendly or localized version of the message.
- `identifier` (string): A unique error identifier code (string) for error cases, combining an error category and error name. For successful operations, this is `"OK"`. This field can be used to programmatically distinguish error types.
- `details` (list): An array of additional detail strings, usually used to provide more context for errors (it might include stack trace info or validation details). This is often empty (`[]`) for successful requests or standard errors.
- `body` (object): The main content of the response. For successful requests, this contains the data payload (e.g. user token data, list of chats, chat or message object, etc., depending on the endpoint). For operations that don’t return additional data (like deletions or heartbeat), body will be `null`. In error cases, body is also typically `null` (since the error information itself is conveyed in the other fields).

**Example of a success response structure** (from a chat listing, truncated for brevity):

```json
{
  "code": 200,
  "message": "OK",
  "body": [ ...data... ],
  "details": [],
  "user_message": "OK",
  "identifier": "OK"
}
```

**Example of an error response structure** (e.g., unauthorized access):

```json
{
  "code": 401,
  "message": "Token expired",
  "body": null,
  "details": [],
  "user_message": "Your session has expired. Please log in again.",
  "identifier": "AUTH_ERRORS@EXPIRED"
}
```

In each of the endpoint examples above, we included the full JSON structure to illustrate the `body` content and surrounding fields. Clients interacting with this API should always check the code (or HTTP status) to determine if the request succeeded, and then use the data in `body` accordingly. The `identifier` and `message` can be used for debugging or displaying error information.

**Note:** All timestamp fields (`created_at`, `last_chat_at`, etc.) are given as Unix timestamps (seconds since epoch). Clients may need to convert these into readable date/time formats as needed. The boolean flags like `is_alive` or `guest` are returned as JSON booleans (`true`/`false`).

## Conclusion

This documentation covers all current endpoints and data models of the chat system’s API. It provides the necessary information to understand how to interact with the API, what inputs to provide, and what outputs to expect for each operation. Each endpoint’s response structure is consistent with the overall API format, making error handling and data parsing straightforward. Use this as a reference when developing clients or integrating with the chat system.