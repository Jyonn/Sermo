class Code:
    Continue = 100
    SwitchingProtocols = 101
    Processing = 102

    OK = 200
    Created = 201
    Accepted = 202
    NonAuthoritativeInformation = 203
    NoContent = 204
    ResetContent = 205
    PartialContent = 206
    MultiStatus = 207

    MultipleChoices = 300
    MovedPermanently = 301
    MoveTemporarily = 302
    SeeOther = 303
    NotModified = 304
    UseProxy = 305
    SwitchProxy = 306  # 不再使用
    TemporaryRedirect = 307

    BadRequest = 400
    Unauthorized = 401
    PaymentRequired = 402
    Forbidden = 403
    NotFound = 404
    MethodNotAllowed = 405
    NotAcceptable = 406
    ProxyAuthenticationRequired = 407
    RequestTimeout = 408
    Conflict = 409
    Gone = 410
    LengthRequired = 411
    PreconditionFailed = 412
    RequestEntityTooLarge = 413
    RequestURITooLong = 414
    UnsupportedMediaType = 415
    RequestedRangeNotSatisfiable = 416
    ExpectationFailed = 417
    ImATeapot = 418
    TooManyConnections = 421
    UnprocessableEntity = 422
    Locked = 423
    FailedDependency = 424
    TooEarly = 425
    UpgradeRequired = 426
    RetryWith = 449
    UnavailableForLegalReasons = 451

    InternalServerError = 500
    NotImplemented = 501
    BadGateway = 502
    ServiceUnavailable = 503
    GatewayTimeout = 504
    HTTPVersionNotSupported = 505
    VariantAlsoNegotiates = 506
    InsufficientStorage = 507
    BandwidthLimitExceeded = 509
    NotExtended = 510

    UnparseableResponseHeaders = 600
