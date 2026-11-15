/* The Subsonic client's public surface. Views import from here, never from a file inside. */

export * from "./types";
export { SubsonicClient, type MusicServer, type SubsonicClientOptions } from "./client";
export {
  API_VERSION,
  AUTH_FORMS,
  AUTH_FORM_IDS,
  CLIENT_NAME,
  authParams,
  normaliseOrigin,
  newSalt,
  protocolParams,
  type AuthForm,
  type Credentials,
  type OriginResult,
} from "./auth";
export {
  NativeTransport,
  WebTransport,
  nativeInvoke,
  type FetchLike,
  type InvokeLike,
  type NativeTransportOptions,
  type StreamOptions,
  type SubsonicRequest,
  type SubsonicTransport,
  type WebTransportOptions,
} from "./transport";
export {
  ClientError,
  NotSubsonicError,
  SubsonicError,
  TransportError,
  describeError,
  SUBSONIC_ERRORS,
} from "./errors";
export {
  DEFAULT_PAGE_SIZE,
  EXTENSIONS,
  MAX_PAGE_SIZE,
  capabilitiesFrom,
  looksLikeUnsupported,
  pageRequest,
  type Capabilities,
} from "./capabilities";