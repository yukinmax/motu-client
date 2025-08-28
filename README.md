### Requirements
- pyenv:
    - Install pyenv requirements:
        ```
        sudo apt update; sudo apt install build-essential libssl-dev zlib1g-dev \
        libbz2-dev libreadline-dev libsqlite3-dev curl \
        libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev
        ```
    - Install pyenv: ```curl https://pyenv.run | bash```
    - Load pyenv automatically:
        ```
        echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
        echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
        echo 'eval "$(pyenv init -)"' >> ~/.bashrc
        ```
    - Load pyenv-virtualenv automatically:
        ```
        echo 'eval "$(pyenv virtualenv-init -)"' >> ~/.bashrc
        ```
    - Restart shell
- Python 3.11.6:
    - ```pyenv install 3.11.6```
    - ```pyenv virtualenv 3.11.6 motu-client-3.11.6```
- Python packages:
    - ```pip install -r requirements.txt```

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
