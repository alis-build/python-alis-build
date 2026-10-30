# Changelog

All notable changes to this package are documented here. The format follows
[Conventional Commits](https://www.conventionalcommits.org), and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0](https://github.com/alis-build/python-alis-build/compare/iam/v0.1.0...iam/v0.2.0) (2026-10-30)


### Bug Fixes

* validate the access token returned by a refresh ([e39cfa2](https://github.com/alis-build/python-alis-build/commit/e39cfa2a3ffcca8cfc5f9a89da0a9c0562299fae))
* keep eap and active_account when forwarding an identity ([5221568](https://github.com/alis-build/python-alis-build/commit/52215685f9d02f1ee711f6fdf2e52a01a3cd15a6))
* reject start_login params that replace the generated state ([3795b4f](https://github.com/alis-build/python-alis-build/commit/3795b4fc3da62ca7ed4c2987eb84e7cd1a819b0d))

### Documentation

* package guide, and CI guards for the packaging promises ([145c068](https://github.com/alis-build/python-alis-build/commit/145c0680e25052701403cf07140b9e58b58eeec4))
* scoped credentials, and why role ids may not cross a service boundary ([e67e24b](https://github.com/alis-build/python-alis-build/commit/e67e24b9ed79dbcbaa69cf4b4bea7ff637874582))
* changelog for iam/v0.1.0 ([f16b3f1](https://github.com/alis-build/python-alis-build/commit/f16b3f1810ad00e26f98d9b208d402f531fb05cc))
* describe which identity fields the encoding omits ([2493f44](https://github.com/alis-build/python-alis-build/commit/2493f448b8b50477f8775006eedd33e50098f030))

### Features

* identity model, wire format and context propagation ([672aae6](https://github.com/alis-build/python-alis-build/commit/672aae67541d15a45a35c5925c97aacbf1f5173e))
* HTTP header primitives and ASGI middleware ([c4fdd58](https://github.com/alis-build/python-alis-build/commit/c4fdd586331e6e02be6cb894ceae94bce6e41e85))
* gRPC metadata helpers and identity interceptors ([abc6802](https://github.com/alis-build/python-alis-build/commit/abc680244c01fd179261ed1212197d8e00070261))
* authorization registry, member resolvers and Authorizer ([827f237](https://github.com/alis-build/python-alis-build/commit/827f2379b6d52fe7e462e69124d5617c7a3ecd64))
* concurrent policy fetching ([75cd7da](https://github.com/alis-build/python-alis-build/commit/75cd7da23141d0cc473cb009fb056435b5eeed2b))
* OAuth2/OIDC client, token verification and login flow ([54c501e](https://github.com/alis-build/python-alis-build/commit/54c501e6d3fb9fc7ad5bb3edd1c96b87d395cbd0))
* scoped credential claims on Identity ([ba977ab](https://github.com/alis-build/python-alis-build/commit/ba977abf62021c3f9f9a2f860ebcc72c27c1952b))
* carry scoped credential claims through the wire format ([0a1618e](https://github.com/alis-build/python-alis-build/commit/0a1618e5e2f990c476ba104049674dca94497e83))
* deny open roles to restricted credentials ([427f299](https://github.com/alis-build/python-alis-build/commit/427f299051f099c70ac5a4212f905cd15194ac62))
* encode the eap and active_account claims ([ed5e63a](https://github.com/alis-build/python-alis-build/commit/ed5e63a9ce41d889e7915e107582919ad4ad5b02))
* report the Early Access Program flag ([4de1801](https://github.com/alis-build/python-alis-build/commit/4de18013e170432c599ef03b4a2ac620d8b0edee))
* resolve the active account id ([4267635](https://github.com/alis-build/python-alis-build/commit/42676358ca082e2bb1a3bd50a9b83055c17f4284))
* report credit standing from the token ([7e1d694](https://github.com/alis-build/python-alis-build/commit/7e1d69463b2d907f1d720a33958c18c025fc2214))
* report the member's credit validity ([cbf2bbf](https://github.com/alis-build/python-alis-build/commit/cbf2bbf5a356ec67a58f41cb2c1cc699a5a94f1c))
* add OpenID Connect sign-in options to the authorize URL ([0e4c2a5](https://github.com/alis-build/python-alis-build/commit/0e4c2a56bf96429e0626e7748e12397554637ab0))
* accept sign-in options when starting a login ([3c02d3d](https://github.com/alis-build/python-alis-build/commit/3c02d3dfa01981b92317cd4ef99230dd11bf2298))
