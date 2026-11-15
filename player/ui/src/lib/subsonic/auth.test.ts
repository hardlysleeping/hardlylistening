import { describe, expect, it } from "vitest";
import { md5Hex } from "./md5";
import { authParams, normaliseOrigin, protocolParams } from "./auth";
import { ClientError } from "./errors";

describe("md5", () => {
  it("matches the RFC 1321 vectors", () => {
    expect(md5Hex("")).toBe("d41d8cd98f00b204e9800998ecf8427e");
    expect(md5Hex("a")).toBe("0cc175b9c0f1b6a831c399e269772661");
    expect(md5Hex("abc")).toBe("900150983cd24fb0d6963f7d28e17f72");
    expect(md5Hex("message digest")).toBe("f96b697d7cb7938d525a2f31aaf161d0");
    expect(md5Hex("abcdefghijklmnopqrstuvwxyz")).toBe("c3fcd3d76192e4007dfb496cca67e13b");
  });

  it("hashes more than one block", () => {
    expect(md5Hex("12345678901234567890123456789012345678901234567890123456789012345678901234567890")).toBe(
      "57edf4a22be3c955ac49da2e2107b67a",
    );
  });

  it("hashes UTF-8, not code units", () => {
    // The protocol sends the password as UTF-8 and hashes the bytes: U+00E9 is c3 a9.
    // Cross-checked against `printf 'é' | md5sum`.
    expect(md5Hex("é")).toBe("66ddcd97cfdeabb2f6fb8a999b4bc76f");
  });
});

describe("token authentication", () => {
  it("is md5(secret + salt) with the salt alongside", () => {
    const params = authParams({ username: "me", secret: "hunter2", form: "token" }, "abcdef0123456789");
    expect(params).toEqual({
      u: "me",
      s: "abcdef0123456789",
      t: md5Hex("hunter2abcdef0123456789"),
    });
    expect(params["p"]).toBeUndefined();
  });

  it("never sends the password on token form", () => {
    const params = authParams({ username: "me", secret: "hunter2", form: "token" }, "1111111111111111");
    expect(Object.keys(params).sort()).toEqual(["s", "t", "u"]);
  });

  it("uses a different salt for every request", () => {
    const first = authParams({ username: "me", secret: "x", form: "token" });
    const second = authParams({ username: "me", secret: "x", form: "token" });
    expect(first["s"]).not.toBe(second["s"]);
    expect(first["t"]).not.toBe(second["t"]);
  });

  it("sends the password for the password and device-invite forms", () => {
    expect(authParams({ username: "me", secret: "pw", form: "password" })).toEqual({ u: "me", p: "pw" });
    expect(authParams({ username: "me", secret: "invite", form: "device-invite" })).toEqual({
      u: "me",
      p: "invite",
    });
  });

  it("refuses an account with no credential", () => {
    expect(() => authParams({ username: "me", secret: "", form: "token" })).toThrow(ClientError);
  });
});

describe("protocol parameters", () => {
  it("says who we are and asks for JSON", () => {
    expect(protocolParams()).toEqual({ v: "1.16.1", c: "hardlylistening-player", f: "json" });
  });
});

describe("server addresses", () => {
  it("accepts a scheme, a host and an optional path", () => {
    expect(normaliseOrigin("https://music.example.com")).toEqual({ origin: "https://music.example.com" });
    expect(normaliseOrigin("http://10.0.0.5:4533/sub")).toEqual({ origin: "http://10.0.0.5:4533/sub" });
    expect(normaliseOrigin(" https://music.example.com/ ")).toEqual({ origin: "https://music.example.com" });
  });

  it("refuses a missing scheme rather than guessing one", () => {
    expect(normaliseOrigin("music.example.com")).toHaveProperty("error");
  });

  it("refuses a pasted /rest with the fix in the message", () => {
    const result = normaliseOrigin("https://music.example.com/rest");
    expect(result).toHaveProperty("error");
    expect("error" in result && result.error).toContain("/rest");
  });

  it("refuses an address carrying a password instead of stripping it", () => {
    const result = normaliseOrigin("https://me:hunter2@music.example.com");
    expect(result).toHaveProperty("error");
    expect("error" in result && result.error).toContain("username or password");
  });

  it("refuses a query string or a fragment", () => {
    expect(normaliseOrigin("https://music.example.com/?x=1")).toHaveProperty("error");
    expect(normaliseOrigin("https://music.example.com/#frag")).toHaveProperty("error");
  });

  it("refuses a scheme that is not http or https", () => {
    expect(normaliseOrigin("ftp://music.example.com")).toHaveProperty("error");
  });
});