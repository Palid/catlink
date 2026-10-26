# CatLink for Home Assistant

Connects a person's CatLink cloud account to Home Assistant so their litter boxes, feeders, fountains and cats can be monitored and controlled.

## Language

### Accounts and access

**Account**:
A CatLink cloud user to whom Devices are bound or shared. It has one stable identity in the cloud regardless of how the person signs in.
_Avoid_: User, profile

**Account Identifier**:
The Email or Phone a person presents, together with a password, to sign in to an Account. One Account may have both.
_Avoid_: Login, username, credentials

**Phone**:
An Account Identifier made of a country calling code and a national number.
_Avoid_: Mobile, number

**Sign-in Method**:
The way a person proves they hold an Account: Email and password, or Phone and password. The CatLink app also offers SMS code and third-party sign-in, which this integration does not.
_Avoid_: Login type, auth mode

**Session**:
The single authenticated presence the CatLink cloud allows an Account at any moment. Signing in anywhere starts a new Session and ends the previous one.
_Avoid_: Token, login

**Eviction**:
The end of a Session caused by the same Account signing in somewhere else, such as the phone app.
_Avoid_: Token expiry, logout, kicked

### What an Account holds

**Device**:
A physical CatLink product, such as a litter box, feeder or fountain, bound or shared to an Account.
_Avoid_: Appliance, machine, unit

**Cat**:
A pet profile on an Account, for which the cloud reports health and visit data. A Cat is not a Device, even though Home Assistant lists both in its device registry.
_Avoid_: Pet device, cat device

### Reaching Devices

**CatLink Cloud**:
The vendor's own service, which holds Accounts and, for older Devices, relays all state and commands.
_Avoid_: CatLink API, the server, the backend

**Aliyun Channel**:
The Alibaba Cloud IoT service through which camera-generation Devices report live state and accept commands. The CatLink Cloud only vouches for the Account there; it does not relay.
_Avoid_: Aliyun API, IoT platform, Living Link, Link Visual

**Thing Property**:
A named value a Device exposes over the Aliyun Channel, read to learn its state and written to command it.
_Avoid_: Attribute, panel data, thing model

**Local Control**:
State and commands exchanged directly between Home Assistant and a Device over the home network, with no cloud involved. No litter box family is known to offer it.
_Avoid_: Offline mode, LAN mode, local-network-only
