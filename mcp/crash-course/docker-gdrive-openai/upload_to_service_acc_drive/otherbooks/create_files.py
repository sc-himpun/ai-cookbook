
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
    ]
}

# Create text files
for filename, titles in author_books.items():
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(titles))

print("Files created successfully.")
