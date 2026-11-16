PYTHON ?= $(shell \
	if [ -x .venv/bin/python ]; then echo .venv/bin/python; \
	elif [ -x ../.venv/bin/python ]; then echo ../.venv/bin/python; \
	else echo python3; fi)

.PHONY: contracts test-contracts check

contracts:
	$(PYTHON) scripts/validate_contracts.py

test-contracts:
	$(PYTHON) -m unittest discover -s tests/contracts -v

check: contracts test-contracts
