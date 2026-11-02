# id_syn_ele: 3 bit đầu của raw_data_block
SYN_ELE_NAMES = {
    0: "SCE (Single Channel Element)",
    1: "CPE (Channel Pair Element)",
    2: "CCE (Coupling Channel Element)",
    3: "LFE (Low Frequency Element)",
    4: "DSE (Data Stream Element)",
    5: "PCE (Program Config Element)",
    6: "FIL (Fill Element)",
    7: "END",
}



def classify(chunk):
    """Phân loại 4 byte dữ liệu AAC sau header ADTS.
    Trả về nhãn dựa trên id_syn_ele (3 bit đầu)."""
    syn_ele = chunk[0] >> 5
    return SYN_ELE_NAMES.get(syn_ele, f"Element {syn_ele}")