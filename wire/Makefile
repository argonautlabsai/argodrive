CC = clang
CFLAGS = -std=c11 -O2 -Wall -Wextra -Werror -pthread
BUILD = ../.build/wire

all: $(BUILD)/argodrive-node $(BUILD)/libargodrive-wire.dylib

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/argodrive-node: node/argodrive-node.c node/cache.h node/pipeline.h protocol.h | $(BUILD)
	$(CC) $(CFLAGS) node/argodrive-node.c -o $@

$(BUILD)/libargodrive-wire.dylib: host/wire_client.c protocol.h | $(BUILD)
	$(CC) $(CFLAGS) -dynamiclib host/wire_client.c -o $@

.PHONY: all
