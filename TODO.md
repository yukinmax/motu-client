# TODO

Roadmap for [motu-bridge](README.md).

- [x] Fix the panel crash caused by meters feedback
- [x] Re-send relevant feedback to panel after it wakes up
- [x] Adjust sleep timeout
- [x] Implement external panel sleep timeout
- [x] Implement proper fader send accuracy
- [ ] Implement panel health check with ping-ack
- [x] Implement panel reconnect/timeout logic
- [ ] Use json-only messages from the panel
- [x] Strip whitespaces from json messages sent to the panel
- [x] Implement proper post-fader level meters
- [x] Implement peaks for level meters
- [x] Add solo button handling
- [ ] Implement protobuf for panel communication
- [x] Handle panel disconnect
- [x] Implement appropriate application shutdown without any leaking resources
- [x] Create Dockerfile and streamline the deployment
- [x] Implement config file
- [ ] Decouple API service, MOTU connection handling and Skaarhoj Panel connection handling
- [x] Replace requests with aiohttp
- [ ] Replace connection-state polling loops in the panel client with asyncio.Event
- [ ] Add a REST endpoint for sending JSON and ASCII commands to the panel
- [ ] Adjust the panel fader curve to match the MOTU mixer or a common console taper, and make it configurable
