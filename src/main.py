import sys
import argparse
from tsl_parser import TSLParser
from cnf_encoder import CNFEncoder

def main():
    # main function to handle command-line arguments and execute the encoder.
    # expected usage: python main.py <tsl_file> [-t T] [-k K] [-o OUTPUT]
    parser = argparse.ArgumentParser(description="tsl to dimacs cnf encoder for combinatorial testing.")
    parser.add_argument("tsl_file", type=str, help="path to the tsl definition file.")
    parser.add_argument("-t", type=int, default=2, help="interaction strength (t-way coverage). default is 2 (pairwise).")
    parser.add_argument("-k", type=int, default=10, help="test suite size (k). the maximum number of test cases. default is 10.")
    parser.add_argument("-o", "--output", type=str, default="output.cnf", help="output file path for the dimacs cnf.")
    
    args = parser.parse_args()

    try:
        # parse tsl file 
        tsl_parser = TSLParser()
        parsed_data = tsl_parser.parse_file(args.tsl_file)
        
        # encode the constraints and coverage goals
        print(f"--- Starting encoding: t={args.t}, k={args.k} ---")
        encoder = CNFEncoder(parsed_data, t=args.t, k=args.k)
        dimacs_output = encoder.encode()
        
        # write output to file
        with open(args.output, 'w') as f:
            f.write(dimacs_output)
            
        print(f"--- Encoding complete ---")
        print(f"Dimacs cnf written to: {args.output}")
        print(f"Total variables: {encoder.next_coverage_id - 1}")
        print(f"Total clauses: {len(encoder.cnf_clauses)}")

    except Exception as e:
        print(f"[!] An error occurred during encoding: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append('../flex/testplans.alt/v5/v0.tsl') 

    main()