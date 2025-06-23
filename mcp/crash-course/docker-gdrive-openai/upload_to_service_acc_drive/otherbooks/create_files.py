# filename: generate_mystery_books.py

author_books = {
    "gk_chesterton.txt": [
        "The Blue Cross",
        "The Secret Garden",
        "The Queer Feet",
        "The Flying Stars",
        "The Invisible Man",
        "The Honour of Israel Gow",
        "The Wrong Shape",
        "The Sins of Prince Saradine",
        "The Hammer of God",
        "The Eye of Apollo",
        "The Sign of the Broken Sword",
        "The Three Tools of Death",
        "The Dagger with Wings",
        "The Curse of the Golden Cross"
    ],
    "raymond_chandler.txt": [
        "The Big Sleep – 1939",
        "Farewell, My Lovely – 1940",
        "The High Window – 1942",
        "The Lady in the Lake – 1943",
        "The Little Sister – 1949",
        "The Long Goodbye – 1953",
        "Playback – 1958",
        "Poodle Springs – 1989 (completed by Robert B. Parker)"
    ],
    "dorothy_sayers.txt": [
        "Whose Body? – 1923",
        "Clouds of Witness – 1926",
        "Unnatural Death – 1927",
        "The Unpleasantness at the Bellona Club – 1928",
        "Strong Poison – 1930",
        "The Five Red Herrings – 1931",
        "Have His Carcase – 1932",
        "Murder Must Advertise – 1933",
        "The Nine Tailors – 1934",
        "Gaudy Night – 1935",
        "Busman’s Honeymoon – 1937"
    ],
    "pd_james.txt": [
        "Cover Her Face – 1962",
        "A Mind to Murder – 1963",
        "Unnatural Causes – 1967",
        "Shroud for a Nightingale – 1971",
        "The Black Tower – 1975",
        "Death of an Expert Witness – 1977",
        "A Taste for Death – 1986",
        "Devices and Desires – 1989",
        "Original Sin – 1994",
        "A Certain Justice – 1997",
        "The Murder Room – 2003",
        "The Private Patient – 2008"
    ],
    "jo_nesbo.txt": [
        "The Bat – 1997",
        "Cockroaches – 1998",
        "The Redbreast – 2000",
        "Nemesis – 2002",
        "The Devil's Star – 2003",
        "The Redeemer – 2005",
        "The Snowman – 2007",
        "The Leopard – 2009",
        "Phantom – 2011",
        "Police – 2013",
        "The Thirst – 2017",
        "Knife – 2019",
        "Killing Moon – 2022"
    ],
    "ellery_queen.txt": [
        "The Roman Hat Mystery – 1929",
        "The French Powder Mystery – 1930",
        "The Dutch Shoe Mystery – 1931",
        "The Greek Coffin Mystery – 1932",
        "The Egyptian Cross Mystery – 1932",
        "The American Gun Mystery – 1933",
        "The Siamese Twin Mystery – 1933",
        "The Chinese Orange Mystery – 1934",
        "The Spanish Cape Mystery – 1935",
        "Halfway House – 1936",
        "The Door Between – 1937",
        "The Devil to Pay – 1938"
    ],
    "georges_simenon.txt": [
        "Pietr the Latvian – 1931",
        "The Hanged Man of Saint-Pholien – 1931",
        "The Yellow Dog – 1931",
        "Night at the Crossroads – 1931",
        "A Man's Head – 1931",
        "The Carter of 'La Providence' – 1931",
        "The Grand Banks Café – 1931",
        "The Dancer at the Gai-Moulin – 1931",
        "Maigret and the Enigmatic Letter – 1931",
        "Maigret Sets a Trap – 1955",
        "Maigret and the Lazy Burglar – 1961",
        "Maigret and the Killer – 1969",
        "Maigret and the Wine Merchant – 1970"
    ],
    "colin_dexter.txt": [
        "Last Bus to Woodstock – 1975",
        "Last Seen Wearing – 1976",
        "The Silent World of Nicholas Quinn – 1977",
        "Service of All the Dead – 1979",
        "The Dead of Jericho – 1981",
        "The Riddle of the Third Mile – 1983",
        "The Secret of Annexe 3 – 1986",
        "The Wench is Dead – 1989",
        "The Jewel That Was Ours – 1991",
        "The Way Through the Woods – 1992",
        "The Daughters of Cain – 1994",
        "Death is Now My Neighbour – 1996",
        "The Remorseful Day – 1999"
    ],
    "tana_french.txt": [
        "In the Woods – 2007",
        "The Likeness – 2008",
        "Faithful Place – 2010",
        "Broken Harbour – 2012",
        "The Secret Place – 2014",
        "The Trespasser – 2016",
        "The Witch Elm – 2018",
        "The Searcher – 2020",
        "The Hunter – 2024"
    ]
}

# Write all files
for filename, books in author_books.items():
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(books))

print("All author files created successfully.")
