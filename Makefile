CXX      ?= g++
CXXFLAGS ?= -O3 -march=native -DNDEBUG
CXXFLAGS += -std=c++17 -Wall -Wextra -pthread
LDFLAGS  += -pthread

SRC  := src/bitboard.cpp src/position.cpp src/movegen.cpp src/evaluate.cpp src/nnue.cpp src/tt.cpp src/search.cpp src/telo.cpp src/uci.cpp
OBJ  := $(SRC:.cpp=.o)
EXE  := fanorona

all: $(EXE)

$(EXE): $(OBJ) src/main.o
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

tests/run_tests: $(OBJ) tests/test_main.o
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

%.o: %.cpp src/*.h
	$(CXX) $(CXXFLAGS) -Isrc -c $< -o $@

test: tests/run_tests
	./tests/run_tests

bench: $(EXE)
	./$(EXE) bench

clean:
	rm -f src/*.o tests/*.o $(EXE) tests/run_tests

.PHONY: all test bench clean
