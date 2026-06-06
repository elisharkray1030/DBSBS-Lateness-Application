import csv
import math

def load_namelist(namelist_filename):
    """Loads valid boarders into a dictionary, ignoring casing and spacing errors."""
    boarders_master = {}

    try:
        with open(namelist_filename, mode='r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row.get('Name', '').strip().upper()
                bed = row.get('Bed', '').strip()

                # Skip rows with missing name or bed information.
                if not name or not bed:
                    continue

                boarders_master[name] = {
                    "bed": bed,
                    "frequency": 0,
                    "total_minutes": 0
                }
        print(f"Successfully loaded {len(boarders_master)} valid boarders from master list.")
        return boarders_master
    except FileNotFoundError:
        print(f"ERROR: Could not find the file '{namelist_filename}'. Please check the filename.")
        return None

def process_lateness(log_filename, boarders_dict):
    """Parses the monthly logs and populates the boarders dictionary with lateness metrics."""
    if not boarders_dict:
        return None

    print(f"Processing monthly logs from {log_filename}...")

    START_SECONDS = (7 * 3600) + (41 * 60)
    END_SECONDS = (8 * 3600) + (0 * 60)

    with open(log_filename, mode='r', encoding='utf-8-sig') as file:
        csv_reader = csv.DictReader(file)

        for row in csv_reader:
            name = row.get('Name', '').strip().upper()

            if not name or name not in boarders_dict:
                continue

            time_str = row.get('Transaction Time', '').strip()

            try:
                time_parts = list(map(int, time_str.split(':')))
                if len(time_parts) == 3:
                    h, m, s = time_parts
                elif len(time_parts) == 2:
                    h, m = time_parts
                    s = 0
                else:
                    continue

                current_seconds = (h * 3600) + (m * 60) + s

                if START_SECONDS < current_seconds <= END_SECONDS:
                    seconds_late = current_seconds - START_SECONDS
                    minutes_late = math.ceil(seconds_late / 60)

                    boarders_dict[name]["frequency"] += 1
                    boarders_dict[name]["total_minutes"] += minutes_late

            except (ValueError, IndexError):
                continue

    return boarders_dict

def export_to_csv(output_filename, boarders_dict):
    """Takes the final calculated data and writes it out to a brand new CSV file."""
    if not boarders_dict:
        print("No data to export.")
        return

    print(f"Exporting results to '{output_filename}'...")

    # Define the headers for our output file
    headers = ['Bed', 'Name', 'Frequency', 'Total Minutes Late', 'Total Points']

    # 'w' mode creates a new file or overwrites an existing one with the same name
    with open(output_filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)

        # 1. Write the column headers first
        writer.writerow(headers)

        # 2. Loop through our dictionary and write each student's data as a row
        for boarder_name, data in boarders_dict.items():
            freq = data['frequency']
            mins = data['total_minutes']
            total_points = freq + mins

            # Construct the row layout matching our headers array
            row_data = [data['bed'], boarder_name, freq, mins, total_points]

            # Write the row to the file
            writer.writerow(row_data)

    print(f"Success! Final report generated and saved as '{output_filename}'")


if __name__ == '__main__':
    # --- RUNNING THE ENTIRE PIPELINE ---
    master_list = load_namelist("namelist.csv")
    populated_data = process_lateness("test_data.csv", master_list)
    export_to_csv("lateness_final_report.csv", populated_data)
