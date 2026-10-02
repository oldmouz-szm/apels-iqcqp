CXX = g++
CXXFLAGS = -O3
LDFLAGS = -static -lgsl -lgslcblas -lm

SRCS = ls_read.cpp ls_no_cons.cpp ls_bin.cpp component.cpp ls_mix_not_dis.cpp ls_balance.cpp call.cpp
BUILD_DIR = build
TARGET = $(BUILD_DIR)/LS-IQCQP

all: $(TARGET)

$(TARGET): $(SRCS)
	mkdir -p $(BUILD_DIR)
	$(CXX) $(SRCS) $(CXXFLAGS) -o $(TARGET) $(LDFLAGS)

clean:
	rm -f $(TARGET)
	rmdir $(BUILD_DIR) 2>/dev/null || true

.PHONY: all clean
