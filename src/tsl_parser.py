import re
import json

class TSLParser:
    def __init__(self):
        self.parsed_data = {
            "parameters": {},
            "environments": {}
        }
        self.current_section = None
        self.current_group_name = None
        self.last_significant_indentation = -1
        self.last_group_indentation = -1

    def _parse_line(self, line):
        current_line_raw_indentation = len(line) - len(line.lstrip(' '))
        stripped_line = line.strip()

        if not stripped_line or stripped_line.startswith('#'):
            return

        # section headers
        if stripped_line == "Parameters:":
            self.current_section = "parameters"
            self.current_group_name = None
            self.last_significant_indentation = -1
            self.last_group_indentation = -1
            return
        elif stripped_line == "Environments:":
            self.current_section = "environments"
            self.current_group_name = None
            self.last_significant_indentation = -1
            self.last_group_indentation = -1
            return

        if not self.current_section:
            return

        # group header detection
        group_header_match = re.match(r'^\s*([^:]+):\s*(?:#\s*([^ ]+.*))?$', line)
        if group_header_match and (self.last_group_indentation == -1 or current_line_raw_indentation <= self.last_group_indentation):
            group_name = group_header_match.group(1).strip()
            flag = group_header_match.group(2).strip() if group_header_match.group(2) else None

            self.current_group_name = group_name
            self.parsed_data[self.current_section][self.current_group_name] = {
                "flag": flag,
                "options": []
            }
            self.last_group_indentation = current_line_raw_indentation
            self.last_significant_indentation = current_line_raw_indentation
            return

        # option line detection
        if self.current_group_name:
            option_line_match = re.match(
                r'^\s+(\S.*?)\s*((?:\[\s*[^\]]+\s*\]\s*)*)(?:#\s*(.+))?$', line)

            if option_line_match:
                option_raw_name = option_line_match.group(1).strip()
                tags_string = option_line_match.group(2).strip()
                comment_after_tags = option_line_match.group(3)

                # clean trailing dot only for *_on. or *_off.
                if option_raw_name.endswith('_on.') or option_raw_name.endswith('_off.'):
                    option_name = option_raw_name[:-1]
                else:
                    option_name = option_raw_name

                option_data = {"name": option_name}

                if tags_string:
                    tags = re.findall(r'\[\s*(.*?)\s*\]', tags_string)
                    for tag in tags:
                        parts = tag.split(None, 1)
                        if len(parts) == 1:
                            option_data[parts[0].lower()] = True
                        elif len(parts) == 2:
                            key = parts[0].lower()
                            value = parts[1]
                            if key == "property":
                                option_data["property"] = value
                            elif key == "if":
                                option_data["condition"] = value
                            else:
                                option_data[key] = value

                if comment_after_tags:
                    option_data["comment"] = comment_after_tags.strip()

                self.parsed_data[self.current_section][self.current_group_name]["options"].append(option_data)
                self.last_significant_indentation = current_line_raw_indentation
                return

        # uncomment for debugging skipped lines
        # print(f"Skipped line: '{line.strip()}' (indent={current_line_raw_indentation})")

    def parse_file(self, file_path):
        with open(file_path, 'r') as f:
            for line in f:
                self._parse_line(line)
        return self.parsed_data


if __name__ == "__main__":
    parser = TSLParser()
    parsed_data = parser.parse_file('../flex/testplans.alt/v5/v0.tsl')
    print(json.dumps(parsed_data, indent=4))