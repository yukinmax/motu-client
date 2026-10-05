### Requirements
 - Docker:
    - Build docker image using Dockerfile.

### TODO:
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
- [ ] Implement appropriate application shutdown without any leaking resources
- [x] Create Dockerfile and streamline the deployment
- [ ] Implement config file
- [ ] Decouple API service, MOTU connection handling and Skaarhoj Panel connection handling
- [ ] Replace requests with aiohttp
